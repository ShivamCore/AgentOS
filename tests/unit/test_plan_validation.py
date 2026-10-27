"""Planner output is untrusted: every malformed or unsafe plan must be rejected."""

from __future__ import annotations

import json

import pytest

from agent.lifecycle import (
    InvalidTransitionError,
    NodeState,
    RunState,
    check_run_transition,
    summarize_run,
)
from agent.planner.graph import PlanPolicy, PlanValidationError, parse_planner_dag


def plan(*steps: dict) -> str:
    return json.dumps({"task_id": "auto_generated", "steps": list(steps)})


def step(sid, deps=(), tools=("file_write",), agent="coder", **extra):
    return {
        "step_id": sid,
        "description": f"do {sid}",
        "required_tools": list(tools),
        "preferred_agent": agent,
        "dependencies": list(deps),
        **extra,
    }


class TestValidPlans:
    def test_valid_dag_parses(self):
        g = parse_planner_dag(
            plan(step("1"), step("2", ["1"]), step("3", ["1"])), "task-1"
        )
        assert g.task_id == "task-1"
        assert g.topological_order() == ["1", "2", "3"]
        assert all(n.state is NodeState.PENDING for n in g.nodes.values())

    def test_markdown_wrapped_json_and_integer_ids(self):
        raw = (
            "Here is the plan:\n```json\n"
            + json.dumps(
                {
                    "steps": [
                        {"step_id": 1, "description": "a"},
                        {"step_id": 2, "description": "b", "dependencies": [1]},
                    ]
                }
            )
            + "\n```"
        )
        g = parse_planner_dag(raw, "t")
        assert g.nodes["2"].dependencies == ["1"]


class TestRejectedPlans:
    @pytest.mark.parametrize(
        "raw,msg",
        [
            ("", "empty"),
            ("not json at all", "not valid JSON"),
            ("[1, 2]", "JSON object"),
            ('{"steps": "nope"}', "missing a 'steps'"),
            ('{"steps": []}', "no steps"),
            ('{"steps": [42]}', "not an object"),
            ('{"steps": [{"step_id": "1"}]}', "invalid"),
            ('{"steps": [{"step_id": "1", "description": "x"', "not valid JSON"),
        ],
    )
    def test_malformed_output(self, raw, msg):
        with pytest.raises(PlanValidationError, match=msg):
            parse_planner_dag(raw, "t")

    def test_duplicate_ids(self):
        with pytest.raises(PlanValidationError, match="duplicate"):
            parse_planner_dag(plan(step("1"), step("1")), "t")

    def test_missing_dependency_is_an_error_not_silently_dropped(self):
        with pytest.raises(PlanValidationError, match="unknown step"):
            parse_planner_dag(plan(step("1", ["ghost"])), "t")

    def test_self_dependency(self):
        with pytest.raises(PlanValidationError, match="itself"):
            parse_planner_dag(plan(step("1", ["1"])), "t")

    def test_cycle(self):
        with pytest.raises(PlanValidationError, match="cycle"):
            parse_planner_dag(
                plan(step("a", ["c"]), step("b", ["a"]), step("c", ["b"])), "t"
            )

    def test_unknown_tool(self):
        with pytest.raises(PlanValidationError, match="unauthorized tool"):
            parse_planner_dag(plan(step("1", tools=["git"])), "t")

    def test_tool_not_allowed_by_policy(self):
        policy = PlanPolicy(allowed_tools=frozenset({"file_write"}))
        with pytest.raises(PlanValidationError, match="terminal"):
            parse_planner_dag(plan(step("1", tools=["terminal"])), "t", policy)

    def test_unknown_agent(self):
        with pytest.raises(PlanValidationError, match="unknown agent"):
            parse_planner_dag(plan(step("1", agent="root")), "t")

    def test_unknown_field_rejected(self):
        # e.g. a planner trying to set execution limits or a runtime status
        with pytest.raises(PlanValidationError, match="unsupported field"):
            parse_planner_dag(plan(step("1", timeout_sec=99999)), "t")
        with pytest.raises(PlanValidationError, match="unsupported field"):
            parse_planner_dag(plan(step("1", status="COMPLETED")), "t")

    def test_invalid_step_id(self):
        with pytest.raises(PlanValidationError):
            parse_planner_dag(plan(step("../etc")), "t")

    def test_too_many_nodes(self):
        with pytest.raises(PlanValidationError, match="limit"):
            parse_planner_dag(
                plan(*[step(str(i)) for i in range(4)]), "t", PlanPolicy(max_nodes=3)
            )

    def test_too_deep(self):
        steps = [step("0")] + [step(str(i), [str(i - 1)]) for i in range(1, 5)]
        with pytest.raises(PlanValidationError, match="depth"):
            parse_planner_dag(plan(*steps), "t", PlanPolicy(max_depth=4))


class TestLifecycle:
    def test_run_transitions(self):
        check_run_transition(RunState.CREATED, RunState.RUNNING)
        check_run_transition(RunState.FAILED, RunState.CREATED)
        with pytest.raises(InvalidTransitionError):
            check_run_transition(RunState.COMPLETED, RunState.RUNNING)
        with pytest.raises(InvalidTransitionError):
            check_run_transition(RunState.CREATED, RunState.COMPLETED)

    def test_node_cannot_skip_ready(self):
        g = parse_planner_dag(plan(step("1")), "t")
        with pytest.raises(InvalidTransitionError):
            g.nodes["1"].transition(NodeState.RUNNING)

    def test_no_duplicate_terminal_transition(self):
        g = parse_planner_dag(plan(step("1")), "t")
        n = g.nodes["1"]
        n.transition(NodeState.READY)
        n.transition(NodeState.RUNNING)
        n.transition(NodeState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            n.transition(NodeState.FAILED)

    def test_summarize(self):
        assert summarize_run([NodeState.COMPLETED] * 2) is RunState.COMPLETED
        assert (
            summarize_run([NodeState.COMPLETED, NodeState.BLOCKED])
            is RunState.PARTIAL_SUCCESS
        )
        assert summarize_run([NodeState.FAILED, NodeState.BLOCKED]) is RunState.FAILED
        assert summarize_run([]) is RunState.FAILED
