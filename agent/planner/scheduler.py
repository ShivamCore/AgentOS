"""
DAG scheduler.

Runs a validated ``TaskGraph`` with a bounded thread pool. The scheduler owns
every state transition (through ``agent.lifecycle``); step runners only return a
``StepOutcome``. Policies:

* A node becomes READY only when all of its dependencies are COMPLETED.
* At most ``max_workers`` nodes are in flight at once; independent READY nodes
  run concurrently.
* Failure propagation: when a node ends FAILED / TIMED_OUT / CANCELLED / BLOCKED,
  every transitive dependent becomes BLOCKED. Independent branches keep running.
* Retries: a failed attempt is retried up to ``max_retries`` times (RUNNING ->
  READY) unless the outcome is marked non-retryable.
* Node timeout: a node running longer than ``node_timeout_sec`` is marked
  TIMED_OUT and its cancel event is set. Python threads cannot be killed, so the
  worker thread is abandoned; anything it does afterwards is bounded by the
  sandbox/LLM timeouts and its late result is ignored.
* Run timeout / cancellation: queued and pending nodes become CANCELLED, running
  nodes are signalled; nodes still running at the run deadline become TIMED_OUT.
* Step budget: at most ``max_steps`` distinct nodes are started; the rest are
  CANCELLED with an explanatory error.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent.lifecycle import (
    NODE_TERMINAL,
    InvalidTransitionError,
    NodeState,
    RunState,
    summarize_run,
)
from agent.planner.graph import StepNode, TaskGraph

logger = logging.getLogger(__name__)


@dataclass
class StepOutcome:
    """Contract every step runner must return."""

    success: bool
    output: str = ""
    error: str | None = None
    files_written: list[str] = field(default_factory=list)
    retryable: bool = True


@dataclass
class StepContext:
    attempt: int  # 1-based
    cancel_event: threading.Event  # set on node timeout, run timeout or cancellation
    deadline: float  # monotonic time by which the step must finish
    previous_error: str | None = None


StepRunner = Callable[[StepNode, StepContext], StepOutcome]


@dataclass(frozen=True)
class SchedulerConfig:
    max_workers: int = 4
    max_retries: int = 1
    node_timeout_sec: float = 300.0
    run_timeout_sec: float = 600.0
    max_steps: int = 50
    poll_interval_sec: float = 0.05

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


@dataclass
class RunReport:
    state: RunState
    node_states: dict[str, str]
    nodes_started: int
    max_observed_concurrency: int
    started_at: float
    finished_at: float
    events: list[tuple[float, str, str]]
    errors: dict[str, str]

    @property
    def duration_sec(self) -> float:
        return self.finished_at - self.started_at


@dataclass
class _Inflight:
    node: StepNode
    cancel: threading.Event
    started: list[
        float
    ]  # filled by the worker when it actually begins (READY -> RUNNING)


class DAGScheduler:
    def __init__(
        self,
        runner: StepRunner,
        config: SchedulerConfig | None = None,
        on_state: Callable[[str, str], None] | None = None,
        on_log: Callable[[str, str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.runner = runner
        self.config = config or SchedulerConfig()
        self._on_state = on_state or (lambda *_: None)
        self._on_log = on_log or (lambda *_: None)
        self.clock = clock
        self._events: list[tuple[float, str, str]] = []
        self._events_lock = threading.Lock()
        self._running = 0
        self._max_running = 0

    # ── helpers ────────────────────────────────────────────────────────────────

    def _emit(self, node: StepNode, state: NodeState) -> None:
        with self._events_lock:
            self._events.append((self.clock(), node.step_id, state.value))
        try:
            self._on_state(node.step_id, state.value)
        except Exception:
            logger.exception("node state callback failed for %s", node.step_id)

    def _log(self, node_id: str, kind: str, msg: str) -> None:
        try:
            self._on_log(node_id, kind, msg)
        except Exception:
            logger.exception("log callback failed for %s", node_id)

    def _move(self, node: StepNode, new: NodeState) -> bool:
        try:
            node.transition(new)
        except InvalidTransitionError:
            return False
        self._emit(node, new)
        return True

    def _block_dependents(self, graph: TaskGraph, failed_id: str) -> None:
        children: dict[str, list[str]] = {}
        for n in graph.nodes.values():
            for d in n.dependencies:
                children.setdefault(d, []).append(n.step_id)
        stack = list(children.get(failed_id, []))
        while stack:
            nid = stack.pop()
            n = graph.nodes[nid]
            if n.state in (NodeState.PENDING, NodeState.READY):
                n.error = f"blocked: dependency '{failed_id}' did not complete"
                if self._move(n, NodeState.BLOCKED):
                    self._log(nid, "error", n.error)
                    stack.extend(children.get(nid, []))

    def _worker(self, item: _Inflight, ctx: StepContext) -> StepOutcome | None:
        node = item.node
        if ctx.cancel_event.is_set() or not self._move(node, NodeState.RUNNING):
            return None  # cancelled while queued
        item.started.append(self.clock())
        with self._events_lock:
            self._running += 1
            self._max_running = max(self._max_running, self._running)
        try:
            outcome = self.runner(node, ctx)
            if not isinstance(outcome, StepOutcome):
                return StepOutcome(
                    False,
                    error=f"step runner returned malformed result: {type(outcome).__name__}",
                )
            return outcome
        except Exception as exc:
            logger.error(
                "node %s raised %s: %s",
                node.step_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return StepOutcome(False, error=f"{type(exc).__name__}: {exc}")
        finally:
            with self._events_lock:
                self._running -= 1

    # ── main loop ──────────────────────────────────────────────────────────────

    def run(
        self, graph: TaskGraph, cancel_event: threading.Event | None = None
    ) -> RunReport:
        cfg = self.config
        cancel_event = cancel_event or threading.Event()
        start = self.clock()
        run_deadline = start + cfg.run_timeout_sec
        inflight: dict[concurrent.futures.Future[Any], _Inflight] = {}
        abandoned: list[concurrent.futures.Future[Any]] = []
        retry_not_before: dict[str, float] = {}
        started_ids: set[str] = set()
        run_state: RunState | None = None
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=cfg.max_workers, thread_name_prefix="dag"
        )

        def finish_unstarted(state: NodeState, reason: str) -> None:
            for n in graph.nodes.values():
                if n.state in (NodeState.PENDING, NodeState.READY) and not any(
                    it.node is n for it in inflight.values()
                ):
                    n.error = reason
                    if self._move(n, state):
                        self._log(n.step_id, "error", reason)

        def handle_done(
            fut: concurrent.futures.Future[Any], it: _Inflight, allow_retry: bool
        ) -> None:
            node = it.node
            outcome = fut.result()  # _worker never raises
            if outcome is None:  # cancelled while still queued
                if node.state is NodeState.READY:
                    self._move(node, NodeState.CANCELLED)
                return
            if node.state is not NodeState.RUNNING:
                return  # late result after a timeout: ignored
            node.output = outcome.output
            node.files_written = list(outcome.files_written)
            if outcome.success:
                node.error = None
                self._move(node, NodeState.COMPLETED)
                return
            node.error = outcome.error or "step failed without an error message"
            self._log(
                node.step_id, "error", f"attempt {node.attempts} failed: {node.error}"
            )
            if allow_retry and outcome.retryable and node.attempts <= cfg.max_retries:
                self._move(node, NodeState.READY)
                retry_not_before[node.step_id] = self.clock()
            else:
                logger.error(
                    "node %s failed after %d attempt(s): %s",
                    node.step_id,
                    node.attempts,
                    node.error,
                )
                self._move(node, NodeState.FAILED)
                self._block_dependents(graph, node.step_id)

        try:
            while True:
                now = self.clock()

                # 1. run-level stop conditions
                stop_reason = None
                if cancel_event.is_set():
                    run_state, stop_reason = RunState.CANCELLED, "run cancelled"
                elif now >= run_deadline:
                    run_state, stop_reason = (
                        RunState.TIMED_OUT,
                        f"run exceeded {cfg.run_timeout_sec}s",
                    )
                if stop_reason:
                    finish_unstarted(NodeState.CANCELLED, stop_reason)
                    for fut, it in list(inflight.items()):
                        it.cancel.set()
                        del inflight[fut]
                        if fut.done():
                            handle_done(fut, it, allow_retry=False)
                            continue
                        final = (
                            NodeState.TIMED_OUT
                            if run_state is RunState.TIMED_OUT
                            else NodeState.CANCELLED
                        )
                        it.node.error = stop_reason
                        if not self._move(
                            it.node, final
                        ):  # still queued (READY): cannot time out
                            self._move(it.node, NodeState.CANCELLED)
                        abandoned.append(fut)
                    break

                # 2. promote PENDING nodes / propagate failures
                for n in graph.nodes.values():
                    if n.state is not NodeState.PENDING:
                        continue
                    dep_states = [graph.nodes[d].state for d in n.dependencies]
                    if all(s is NodeState.COMPLETED for s in dep_states):
                        self._move(n, NodeState.READY)
                    elif any(
                        s in NODE_TERMINAL and s is not NodeState.COMPLETED
                        for s in dep_states
                    ):
                        bad = next(
                            d
                            for d in n.dependencies
                            if graph.nodes[d].state in NODE_TERMINAL
                            and graph.nodes[d].state is not NodeState.COMPLETED
                        )
                        self._block_dependents(graph, bad)

                # 3. dispatch READY nodes within the concurrency and step budgets
                queued = {it.node.step_id for it in inflight.values()}
                for n in graph.nodes.values():
                    if len(inflight) >= cfg.max_workers:
                        break
                    if n.state is not NodeState.READY or n.step_id in queued:
                        continue
                    if retry_not_before.get(n.step_id, 0.0) > now:
                        continue
                    if n.step_id not in started_ids:
                        if len(started_ids) >= cfg.max_steps:
                            n.error = (
                                f"step budget exhausted (max_steps={cfg.max_steps})"
                            )
                            if self._move(n, NodeState.CANCELLED):
                                self._log(n.step_id, "error", n.error)
                                self._block_dependents(graph, n.step_id)
                            continue
                        started_ids.add(n.step_id)
                    n.attempts += 1
                    cancel = threading.Event()
                    ctx = StepContext(
                        attempt=n.attempts,
                        cancel_event=cancel,
                        deadline=min(run_deadline, now + cfg.node_timeout_sec),
                        previous_error=n.error,
                    )
                    item = _Inflight(node=n, cancel=cancel, started=[])
                    inflight[pool.submit(self._worker, item, ctx)] = item

                # 4. termination / impossible-state detection
                if not inflight:
                    if all(n.state in NODE_TERMINAL for n in graph.nodes.values()):
                        break
                    waiting_retry = any(
                        n.state is NodeState.READY
                        and retry_not_before.get(n.step_id, 0.0) > now
                        for n in graph.nodes.values()
                    )
                    if not waiting_retry and not any(
                        n.state is NodeState.READY for n in graph.nodes.values()
                    ):
                        for n in graph.nodes.values():
                            if n.state not in NODE_TERMINAL:
                                n.error = (
                                    "scheduler deadlock: node can never become ready"
                                )
                                logger.error("node %s: %s", n.step_id, n.error)
                                self._move(n, NodeState.BLOCKED)
                        break

                # 5. wait for progress, then handle node timeouts and outcomes
                timeout = cfg.poll_interval_sec
                done, _ = concurrent.futures.wait(
                    list(inflight),
                    timeout=timeout,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                now = self.clock()
                for fut, it in list(inflight.items()):
                    if fut in done or not it.started:
                        continue
                    if now - it.started[0] > cfg.node_timeout_sec:
                        it.cancel.set()
                        it.node.error = (
                            f"step exceeded node timeout of {cfg.node_timeout_sec}s"
                        )
                        if self._move(it.node, NodeState.TIMED_OUT):
                            self._log(it.node.step_id, "error", it.node.error)
                            self._block_dependents(graph, it.node.step_id)
                        abandoned.append(fut)
                        del inflight[fut]

                for fut in done:
                    if fut in inflight:
                        handle_done(
                            fut,
                            inflight.pop(fut),
                            allow_retry=not cancel_event.is_set(),
                        )
        except BaseException:
            # e.g. Celery's SoftTimeLimitExceeded: signal running steps and do not block on them.
            for it in inflight.values():
                it.cancel.set()
            abandoned.extend(inflight)
            raise
        finally:
            pool.shutdown(wait=not abandoned, cancel_futures=True)

        if run_state is None:
            run_state = summarize_run([n.state for n in graph.nodes.values()])
        finished = self.clock()
        return RunReport(
            state=run_state,
            node_states=graph.states(),
            nodes_started=len(started_ids),
            max_observed_concurrency=self._max_running,
            started_at=start,
            finished_at=finished,
            events=list(self._events),
            errors={nid: n.error for nid, n in graph.nodes.items() if n.error},
        )


def summarize_report(report: RunReport) -> dict[str, Any]:
    return {
        "state": report.state.value,
        "node_states": report.node_states,
        "nodes_started": report.nodes_started,
        "max_observed_concurrency": report.max_observed_concurrency,
        "duration_sec": round(report.duration_sec, 3),
        "errors": report.errors,
    }
