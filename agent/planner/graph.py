"""
Plan model and validation.

The planner is an LLM and therefore untrusted. ``parse_planner_dag`` turns raw
model text into a ``TaskGraph`` only if every check in ``validate_plan`` passes:
schema (unknown fields rejected), unique ids, known dependencies, no cycles,
size and depth limits, known agents, and tools that exist in the registry and
are allowed by policy. Anything else raises ``PlanValidationError``; nothing is
silently repaired (dangling dependencies are an error, not dropped).
"""

from __future__ import annotations

import json
import re

from dataclasses import dataclass, field
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
)

from agent.lifecycle import NodeState, NodeStateMachine

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_ALLOWED_TOOLS = frozenset({"file_read", "file_write", "terminal"})
DEFAULT_ALLOWED_AGENTS = frozenset({"coder", "debugger"})


class PlanValidationError(ValueError):
    """The planner's output is not an acceptable execution graph."""


@dataclass(frozen=True)
class PlanPolicy:
    max_nodes: int = 15
    max_depth: int = 10
    max_description_chars: int = 2000
    allowed_tools: frozenset[str] = DEFAULT_ALLOWED_TOOLS
    allowed_agents: frozenset[str] = DEFAULT_ALLOWED_AGENTS
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


class StepNode(BaseModel):
    """One atomic step. Planner-supplied fields are strictly validated."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    step_id: str
    description: str = Field(..., min_length=1)
    required_tools: list[str] = Field(default_factory=list)
    preferred_agent: str = "coder"
    dependencies: list[str] = Field(default_factory=list)

    # Runtime fields (never accepted from the planner; see parse_planner_dag).
    attempts: int = 0
    error: str | None = None
    output: str | None = None
    files_written: list[str] = Field(default_factory=list)
    _sm: NodeStateMachine = PrivateAttr(default_factory=NodeStateMachine)

    @field_validator("step_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"invalid step_id {v!r}")
        return v

    @property
    def state(self) -> NodeState:
        return self._sm.state

    @property
    def status(self) -> str:
        return self._sm.state.value

    @property
    def state_history(self) -> list[NodeState]:
        return list(self._sm.history)

    def transition(self, new: NodeState) -> None:
        self._sm.transition(new)


_PLANNER_FIELDS = {
    "step_id",
    "description",
    "required_tools",
    "preferred_agent",
    "dependencies",
}


class TaskGraph(BaseModel):
    task_id: str
    nodes: dict[str, StepNode] = Field(default_factory=dict)

    def is_complete(self) -> bool:
        return bool(self.nodes) and all(
            n.state is NodeState.COMPLETED for n in self.nodes.values()
        )

    def has_failures(self) -> bool:
        return any(
            n.state in (NodeState.FAILED, NodeState.TIMED_OUT)
            for n in self.nodes.values()
        )

    def states(self) -> dict[str, str]:
        return {nid: n.status for nid, n in self.nodes.items()}

    def topological_order(self) -> list[str]:
        return _toposort(self.nodes)


def _toposort(nodes: dict[str, StepNode]) -> list[str]:
    indeg = {nid: len(n.dependencies) for nid, n in nodes.items()}
    children: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, n in nodes.items():
        for d in n.dependencies:
            children[d].append(nid)
    queue = sorted(nid for nid, k in indeg.items() if k == 0)
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for c in sorted(children[cur]):
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(nodes):
        cyclic = sorted(set(nodes) - set(order))
        raise PlanValidationError(f"dependency cycle among steps: {cyclic}")
    return order


def validate_plan(graph: TaskGraph, policy: PlanPolicy | None = None) -> None:
    policy = policy or PlanPolicy()
    nodes = graph.nodes
    if not nodes:
        raise PlanValidationError("plan has no steps")
    if len(nodes) > policy.max_nodes:
        raise PlanValidationError(
            f"plan has {len(nodes)} steps; limit is {policy.max_nodes}"
        )
    for nid, n in nodes.items():
        if nid != n.step_id:
            raise PlanValidationError(
                f"node key {nid!r} does not match step_id {n.step_id!r}"
            )
        if len(n.description) > policy.max_description_chars:
            raise PlanValidationError(
                f"step {nid} description exceeds {policy.max_description_chars} chars"
            )
        if len(set(n.dependencies)) != len(n.dependencies):
            raise PlanValidationError(f"step {nid} lists a dependency twice")
        if nid in n.dependencies:
            raise PlanValidationError(f"step {nid} depends on itself")
        missing = [d for d in n.dependencies if d not in nodes]
        if missing:
            raise PlanValidationError(
                f"step {nid} depends on unknown step(s) {missing}"
            )
        unknown_tools = sorted(set(n.required_tools) - policy.allowed_tools)
        if unknown_tools:
            raise PlanValidationError(
                f"step {nid} requests unavailable/unauthorized tool(s) {unknown_tools}"
            )
        if n.preferred_agent not in policy.allowed_agents:
            raise PlanValidationError(
                f"step {nid} requests unknown agent {n.preferred_agent!r}"
            )
    order = _toposort(nodes)  # raises on cycles
    depth: dict[str, int] = {}
    for nid in order:
        depth[nid] = 1 + max((depth[d] for d in nodes[nid].dependencies), default=0)
    if max(depth.values()) > policy.max_depth:
        raise PlanValidationError(
            f"dependency chain depth {max(depth.values())} exceeds {policy.max_depth}"
        )


def extract_json_from_text(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_planner_dag(
    llm_output: str, fallback_task_id: str, policy: PlanPolicy | None = None
) -> TaskGraph:
    """Parse and validate planner output. Raises PlanValidationError on any problem."""
    if not isinstance(llm_output, str) or not llm_output.strip():
        raise PlanValidationError("planner returned empty output")
    try:
        data = json.loads(extract_json_from_text(llm_output))
    except json.JSONDecodeError as exc:
        # Models sometimes append a second object or prose after the plan: take the first JSON value.
        from agent.llm import extract_json_safely

        data = extract_json_safely(llm_output)
        if data is None:
            raise PlanValidationError(
                f"planner output is not valid JSON: {exc}"
            ) from exc
    if not isinstance(data, dict):
        raise PlanValidationError(
            "planner output must be a JSON object with a 'steps' list"
        )
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise PlanValidationError("planner output is missing a 'steps' list")

    graph = TaskGraph(task_id=fallback_task_id)
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"step #{i} is not an object")
        extra = set(raw) - _PLANNER_FIELDS
        if extra:
            raise PlanValidationError(
                f"step #{i} has unsupported field(s) {sorted(extra)}"
            )
        # Small models often emit integer ids; normalise the type only (bools are not ids).
        raw = dict(raw)
        if isinstance(raw.get("step_id"), int) and not isinstance(
            raw.get("step_id"), bool
        ):
            raw["step_id"] = str(raw["step_id"])
        if isinstance(raw.get("dependencies"), list):
            raw["dependencies"] = [
                str(d) if isinstance(d, int) and not isinstance(d, bool) else d
                for d in raw["dependencies"]
            ]
        try:
            node = StepNode(**raw)
        except ValidationError as exc:
            raise PlanValidationError(
                f"step #{i} is invalid: {exc.errors(include_url=False)}"
            ) from exc
        if node.step_id in graph.nodes:
            raise PlanValidationError(f"duplicate step_id {node.step_id!r}")
        graph.nodes[node.step_id] = node
    validate_plan(graph, policy)
    return graph
