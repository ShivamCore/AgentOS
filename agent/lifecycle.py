"""
AgentOS — Authoritative lifecycle model
========================================
Single source of truth for node (DAG step) and run (task) states and the
transitions allowed between them. The scheduler, worker, API and CLI all
import from here; nothing else defines status strings.

Node lifecycle::

    PENDING ──► READY ──► RUNNING ──► COMPLETED
       │          │          ├──────► FAILED
       │          │          ├──────► TIMED_OUT
       │          │          ├──────► CANCELLED
       │          │          └──────► READY        (retry)
       ├──────────┴────────────────► BLOCKED       (a dependency did not complete)
       └──────────┴────────────────► CANCELLED     (run cancelled / budget exhausted)

Terminal node states never transition again.
"""

from __future__ import annotations

import enum
import threading


class NodeState(str, enum.Enum):
    PENDING = "PENDING"        # waiting on dependencies
    READY = "READY"            # dependencies satisfied, waiting for a worker slot
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"        # never ran because a dependency did not complete


class RunState(str, enum.Enum):
    CREATED = "CREATED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"              # every node COMPLETED
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"  # some nodes COMPLETED, some did not
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


NODE_TERMINAL = frozenset(
    {NodeState.COMPLETED, NodeState.FAILED, NodeState.TIMED_OUT, NodeState.CANCELLED, NodeState.BLOCKED}
)
RUN_TERMINAL = frozenset(
    {RunState.COMPLETED, RunState.PARTIAL_SUCCESS, RunState.FAILED, RunState.CANCELLED, RunState.TIMED_OUT}
)
RUN_ACTIVE = frozenset({RunState.CREATED, RunState.PLANNED, RunState.RUNNING})

_NODE_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.PENDING: frozenset({NodeState.READY, NodeState.BLOCKED, NodeState.CANCELLED}),
    NodeState.READY: frozenset({NodeState.RUNNING, NodeState.BLOCKED, NodeState.CANCELLED}),
    NodeState.RUNNING: frozenset(
        {NodeState.COMPLETED, NodeState.FAILED, NodeState.TIMED_OUT, NodeState.CANCELLED, NodeState.READY}
    ),
}

_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.PLANNED, RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.PLANNED: frozenset({RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(RUN_TERMINAL),
    # Only FAILED / TIMED_OUT / CANCELLED / PARTIAL_SUCCESS runs may be re-queued.
    RunState.FAILED: frozenset({RunState.CREATED}),
    RunState.TIMED_OUT: frozenset({RunState.CREATED}),
    RunState.CANCELLED: frozenset({RunState.CREATED}),
    RunState.PARTIAL_SUCCESS: frozenset({RunState.CREATED}),
}


class InvalidTransition(RuntimeError):
    pass


def check_node_transition(old: NodeState, new: NodeState) -> None:
    if new not in _NODE_TRANSITIONS.get(old, frozenset()):
        raise InvalidTransition(f"node transition {old.value} -> {new.value} is not allowed")


def check_run_transition(old: RunState, new: RunState) -> None:
    if new not in _RUN_TRANSITIONS.get(old, frozenset()):
        raise InvalidTransition(f"run transition {old.value} -> {new.value} is not allowed")


def summarize_run(node_states: list[NodeState]) -> RunState:
    """Derive the final run state from terminal node states."""
    if node_states and all(s is NodeState.COMPLETED for s in node_states):
        return RunState.COMPLETED
    if any(s is NodeState.COMPLETED for s in node_states):
        return RunState.PARTIAL_SUCCESS
    return RunState.FAILED


class NodeStateMachine:
    """Thread-safe holder for one node's state; rejects illegal transitions."""

    __slots__ = ("_lock", "_state", "history")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = NodeState.PENDING
        self.history: list[NodeState] = [NodeState.PENDING]

    @property
    def state(self) -> NodeState:
        return self._state

    def transition(self, new: NodeState) -> None:
        with self._lock:
            check_node_transition(self._state, new)
            self._state = new
            self.history.append(new)
