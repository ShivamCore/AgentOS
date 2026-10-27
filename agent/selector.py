"""
Agent selection, prompting and planning.

* ``get_agent`` maps a role ("plan" | "code" | "debug") to a Markdown agent
  manifest from ``agents/`` and records the decision for ``/tasks/{id}/explain``.
* ``execute_markdown_agent`` makes one LLM call for a step.
* ``plan_markdown_task`` asks the planner for a DAG and validates it; after a
  bounded number of attempts it raises ``PlannerError`` (no fabricated fallback plan).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from agent.llm import LLMResponseError, generate_text
from agent.loader import AgentLoader, AgentManifest
from agent.planner.graph import PlanPolicy, PlanValidationError, TaskGraph, parse_planner_dag
from agent.utils.model_router import select_model as _route_model

logger = logging.getLogger(__name__)

AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
_ROLE_TO_AGENT = {"plan": "planner", "code": "coder", "debug": "debugger"}


class PlannerError(RuntimeError):
    """The planner could not produce a valid plan within its attempt budget."""


@dataclass
class SelectionResult:
    agent_name: str
    confidence: float
    reason: str
    manifest: AgentManifest | None


class AgentSelector:
    def __init__(self, agents_dir: str = AGENTS_DIR) -> None:
        self.loader = AgentLoader(agents_dir)

    def select_agent(self, task_type: str, task_id: str = "system") -> SelectionResult:
        self.loader.load_all()
        name = _ROLE_TO_AGENT.get(task_type)
        if name is None:
            raise ValueError(f"unknown agent role {task_type!r}; expected one of {sorted(_ROLE_TO_AGENT)}")
        manifest = self.loader.agents.get(name)
        if manifest is None:
            status = self.loader.agent_status.get(name, {})
            raise RuntimeError(f"agent manifest '{name}.md' is missing or invalid: {status.get('error')}")
        res = SelectionResult(name, 1.0, f"role '{task_type}' maps to agent '{name}'", manifest)
        self.log_selection(task_id, task_type, res)
        return res

    def log_selection(self, task_id: str, input_hash_src: str, res: SelectionResult) -> None:
        if task_id == "system":
            return
        try:
            from backend.db.database import SessionLocal
            from backend.models.sql_models import AgentSelectionLogRecord

            with SessionLocal() as db:
                db.add(AgentSelectionLogRecord(
                    task_id=task_id,
                    input_hash=hashlib.sha256(input_hash_src.encode()).hexdigest()[:32],
                    selected_agent=res.agent_name,
                    confidence=int(res.confidence * 100),
                    reason=res.reason,
                ))
                db.commit()
        except Exception:
            logger.debug("agent selection telemetry not recorded", exc_info=True)


_global_selector = AgentSelector()


def get_agent(task_type: str, task_id: str = "system") -> SelectionResult:
    return _global_selector.select_agent(task_type, task_id)


def get_selection_log(limit: int = 50) -> list[dict]:
    from backend.db.database import SessionLocal
    from backend.models.sql_models import AgentSelectionLogRecord

    with SessionLocal() as db:
        logs = db.query(AgentSelectionLogRecord).order_by(AgentSelectionLogRecord.timestamp.desc()).limit(limit).all()
        return [
            {"task_id": rec.task_id, "selected_agent": rec.selected_agent, "confidence": rec.confidence,
             "reason": rec.reason, "timestamp": rec.timestamp.isoformat() if rec.timestamp else None}
            for rec in logs
        ]


def check_token_budget(prompt: str, max_input: int) -> str:
    """Truncate a prompt to roughly ``max_input`` tokens (4 chars/token heuristic)."""
    if len(prompt) // 4 > max_input:
        logger.warning("prompt exceeds token budget (%d > %d); truncating", len(prompt) // 4, max_input)
        return prompt[: max_input * 4]
    return prompt


def _resolve_model(manifest: AgentManifest, requested: str, role: str) -> str:
    if requested and requested.lower() != "auto":
        return requested
    if manifest.model and manifest.model.lower() != "auto":
        return manifest.model
    return _route_model(task_type=role)


def execute_markdown_agent(
    task_type: str,
    step_description: str,
    error_or_context: str = "",
    workspace_context: str = "",
    model: str = "Auto",
    stream_callback: Callable[[str], None] | None = None,
    task_id: str = "system",
) -> str:
    agent = get_agent(task_type, task_id).manifest
    assert agent is not None
    if task_type == "code":
        example = '{"files":[{"path":"add.py","code":"def add(a, b):\\n    return a + b\\n"}],"command":null}'
        prompt = f"Example output:\n{example}\n\nNow output the JSON for this step: {step_description}"
        if error_or_context:
            prompt += f"\nOverall task: {error_or_context}"
        temp = 0.0
    elif task_type == "debug":
        prompt = f"STEP: {step_description}\n\nPREVIOUS ATTEMPT FAILED WITH:\n{error_or_context}"
        temp = 0.1
    else:
        prompt = f"Task: {step_description}\nContext: {error_or_context}"
        temp = 0.2
    if workspace_context:
        prompt += f"\n\nWORKSPACE FILES:\n{workspace_context}"
    return generate_text(
        check_token_budget(prompt, agent.max_input_tokens),
        system_prompt=agent.system_prompt,
        temperature=temp,
        model=_resolve_model(agent, model, task_type),
        stream_callback=stream_callback,
        max_tokens=agent.max_output_tokens,
        task_type=task_type,
        json_mode=True,
    )


def plan_markdown_task(
    task_description: str,
    model: str = "Auto",
    task_id: str = "system",
    policy: PlanPolicy | None = None,
    attempts: int = 2,
) -> TaskGraph:
    """Ask the planner LLM for a DAG; validate it. Raises PlannerError after ``attempts`` failures."""
    agent = get_agent("plan", task_id).manifest
    assert agent is not None
    policy = policy or PlanPolicy()
    base = (
        f"Allowed tools: {sorted(policy.allowed_tools)}. Allowed agents: {sorted(policy.allowed_agents)}. "
        f"At most {policy.max_nodes} steps.\n"
        "Output the strict JSON DAG plan for:\n" + task_description
    )
    errors: list[str] = []
    for attempt in range(max(1, attempts)):
        prompt = base if not errors else f"{base}\n\nYour previous plan was rejected: {errors[-1]}\nFix it."
        try:
            text = generate_text(
                check_token_budget(prompt, agent.max_input_tokens),
                system_prompt=agent.system_prompt,
                # A deterministic failure would repeat at temperature 0, so retries sample.
                temperature=0.0 if attempt == 0 else 0.3,
                model=_resolve_model(agent, model, "plan"),
                max_tokens=agent.max_output_tokens,
                task_type="plan",
                use_cache=False,
                json_mode=True,
            )
            return parse_planner_dag(text, fallback_task_id=task_id, policy=policy)
        except (PlanValidationError, LLMResponseError) as exc:
            logger.warning("planner attempt %d rejected: %s", attempt + 1, exc)
            errors.append(str(exc))
    raise PlannerError(f"planner failed to produce a valid plan after {len(errors)} attempt(s): {errors[-1]}")
