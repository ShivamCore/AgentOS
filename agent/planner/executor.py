"""
DAGOrchestrator — runs a validated plan with real LLM-driven steps.

The orchestrator wires three things together:

* ``DAGScheduler`` (dependency ordering, concurrency, retries, timeouts,
  cancellation, failure propagation),
* ``LLMStepRunner`` (one LLM call -> validated ``StepAction`` -> ``execute_step``),
* callbacks the worker uses to persist state, logs and file edits.

The step runner is injectable so tests and benchmarks can run the full
scheduler/executor path with a deterministic runner and no LLM.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

from agent.executor import execute_step, extract_json_payload
from agent.planner.graph import StepNode, TaskGraph
from agent.planner.scheduler import DAGScheduler, RunReport, SchedulerConfig, StepContext, StepOutcome, StepRunner
from agent.selector import execute_markdown_agent, get_agent
from agent.tools.registry import registry

logger = logging.getLogger(__name__)

# risk_level -> tools that may be authorized at that level
RISK_TOOL_POLICY: dict[str, frozenset[str]] = {
    "safe": frozenset({"file_read", "file_write"}),
    "balanced": frozenset({"file_read", "file_write", "terminal"}),
    "aggressive": frozenset({"file_read", "file_write", "terminal"}),
}


def memory_enabled() -> bool:
    # Off by default: ChromaDB's default embedder downloads a model on first use.
    return os.getenv("AGENTOS_MEMORY_ENABLED", "0") == "1"


def workspace_listing(workspace_dir: str, limit: int = 50) -> str:
    paths: list[str] = []
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            paths.append(os.path.relpath(os.path.join(root, f), workspace_dir))
            if len(paths) >= limit:
                return "\n".join(sorted(paths))
    return "\n".join(sorted(paths))


class LLMStepRunner:
    """Default step runner: one LLM call and one sandboxed execution per attempt."""

    def __init__(
        self,
        workspace_dir: str,
        task_id: str,
        user_task: str = "",
        model: str = "Auto",
        risk_level: str = "balanced",
        file_scope: list[str] | None = None,
        log_callback: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.task_id = task_id
        self.user_task = user_task
        self.model = model
        self.risk_tools = RISK_TOOL_POLICY.get(risk_level, RISK_TOOL_POLICY["safe"])
        self.file_scope = list(file_scope or [])
        self.log = log_callback or (lambda *_: None)

    def allowed_tools(self, node: StepNode, agent_tools: list[str]) -> set[str]:
        requested = set(node.required_tools) or set(agent_tools)
        return requested & set(agent_tools) & self.risk_tools & registry.names

    def __call__(self, node: StepNode, ctx: StepContext) -> StepOutcome:
        role = "code" if ctx.attempt == 1 else "debug"
        selection = get_agent(role, self.task_id)
        agent_tools = list(selection.manifest.tools) if selection.manifest else []
        allowed = self.allowed_tools(node, agent_tools)
        self.log(node.step_id, "action",
                 f"attempt {ctx.attempt}: agent={selection.agent_name} tools={sorted(allowed)}")

        context = workspace_listing(self.workspace_dir)
        if memory_enabled():
            from agent.memory.engine import get_memory_engine

            mems = get_memory_engine(self.workspace_dir).search_memory(node.description, limit=3)
            if mems:
                context += "\n\nRELEVANT PAST MEMORY:\n" + "\n".join(m.document for m in mems)

        if ctx.cancel_event.is_set():
            return StepOutcome(False, error="cancelled before LLM call", retryable=False)
        response = execute_markdown_agent(
            task_type=role,
            step_description=node.description,
            error_or_context=(ctx.previous_error or "") if role == "debug" else self.user_task,
            workspace_context=context,
            model=self.model,
            task_id=self.task_id,
        )
        if ctx.cancel_event.is_set():
            return StepOutcome(False, error="cancelled after LLM call; output discarded", retryable=False)

        parsed = extract_json_payload(response)
        if parsed.get("error"):
            return StepOutcome(False, error=parsed["error"])
        result = execute_step(parsed, self.workspace_dir, allowed_tools=allowed, file_scope=self.file_scope)
        if not result["success"]:
            return StepOutcome(False, output=result["stdout"], error=result["stderr"])
        if memory_enabled():
            from agent.memory.engine import get_memory_engine

            get_memory_engine(self.workspace_dir).store_memory(
                selection.agent_name, "task", f"TASK:\n{node.description}\n\nRESOLUTION:\n{response}"
            )
        return StepOutcome(True, output=result["stdout"], files_written=result["files_written"])


class DAGOrchestrator:
    def __init__(
        self,
        workspace_dir: str,
        task_id: str,
        max_retries: int = 2,
        max_workers: int = 4,
        max_time: int = 300,
        max_steps: int = 15,
        risk_level: str = "balanced",
        file_scope: list[str] | None = None,
        node_timeout: int | None = None,
        model: str = "Auto",
        user_task: str = "",
        log_callback: Callable[[str, str, str], None] | None = None,
        node_callback: Callable[[str, str], None] | None = None,
        file_callback: Callable[[str, str, str], None] | None = None,
        step_runner: StepRunner | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.task_id = task_id
        self.max_retries = max_retries
        self.max_workers = max_workers
        self.max_time = max_time
        self.max_steps = max_steps
        self.risk_level = risk_level
        self.file_scope: list[str] = list(file_scope or [])
        self.node_timeout = node_timeout or max_time
        self.log_callback = log_callback or (lambda *_: None)
        self.node_callback = node_callback or (lambda *_: None)
        self.file_callback = file_callback or (lambda *_: None)
        self.step_runner: StepRunner = step_runner or LLMStepRunner(
            workspace_dir, task_id, user_task=user_task, model=model, risk_level=risk_level,
            file_scope=self.file_scope, log_callback=self.log_callback,
        )
        self.start_ts: float = 0.0
        self.total_nodes_executed: int = 0
        self.report: RunReport | None = None

    def _wrapped_runner(self, node: StepNode, ctx: StepContext) -> StepOutcome:
        outcome = self.step_runner(node, ctx)
        if isinstance(outcome, StepOutcome) and outcome.success:
            for rel in outcome.files_written:
                try:
                    with open(os.path.join(self.workspace_dir, rel), encoding="utf-8", errors="replace") as f:
                        self.file_callback(node.step_id, rel, f.read())
                except OSError:
                    logger.warning("could not read written file %s", rel)
        return outcome

    def run_graph(self, graph: TaskGraph, cancel_event: threading.Event | None = None) -> RunReport:
        self.start_ts = time.time()
        scheduler = DAGScheduler(
            self._wrapped_runner,
            SchedulerConfig(
                max_workers=self.max_workers,
                max_retries=self.max_retries,
                node_timeout_sec=float(self.node_timeout),
                run_timeout_sec=float(self.max_time),
                max_steps=self.max_steps,
            ),
            on_state=self.node_callback,
            on_log=self.log_callback,
        )
        self.report = scheduler.run(graph, cancel_event)
        self.total_nodes_executed = self.report.nodes_started
        return self.report
