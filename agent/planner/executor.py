from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
from rich.console import Console
from agent.agent_pool import AgentPool
from agent.selector import execute_markdown_agent, get_agent
from agent.executor import extract_json_payload, execute_step
from agent.planner.graph import TaskGraph, StepNode
from agent.memory.engine import get_memory_engine
from backend.models.sql_models import TaskNodeRecord
from backend.db.database import SessionLocal

console = Console()

class DAGOrchestrator:
    """
    Advanced Execution Engine tracking explicit State Machine bounds mapping safely over independent concurrent paths natively orchestrating Planner logic.
    """
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
        log_callback: Callable | None = None,
        node_callback: Callable | None = None
    ) -> None:
        self.workspace_dir = workspace_dir
        self.task_id = task_id
        self.max_retries = max_retries
        self.max_time = max_time
        self.max_steps = max_steps
        self.risk_level = risk_level
        self.file_scope: list[str] = file_scope or []
        self.pool = AgentPool(max_workers=max_workers)
        self.mem_engine = get_memory_engine(self.workspace_dir)
        
        self.log_callback: Callable = log_callback or (lambda *args: None)
        # node_callback(node_id: str, status: str)
        self.node_callback: Callable = node_callback or (lambda *args: None)
        self.lock = threading.Lock()
        self.total_nodes_executed: int = 0
        self.start_ts: float = 0.0  # Set at run_graph() call time

    def _execute_step(self, node: StepNode) -> bool:
        """
        Independent atomic pipeline. 
        Flow: Selector -> Context -> Execute Tool Sandbox -> Memory Storage
        """
        node.status = "running"
        self.node_callback(node.step_id, "RUNNING")
        self.log_callback(node.step_id, "action", f"▶ Starting DAG node: {node.description}")
        console.print(f"[bold cyan]▶ Node {node.step_id}: {node.description}[/bold cyan]")
        
        attempts = 0
        last_error = ""
        
        while attempts <= self.max_retries:
            # 1. Agent Selection & Fallback Logic dynamically mapping requirements
            preferred = node.preferred_agent if attempts == 0 else "debugger"
            sel_result = get_agent("code", self.task_id)
            selected_agent = sel_result.agent_name if attempts == 0 and sel_result.confidence > 0.6 else preferred
            self.log_callback(node.step_id, "action", f"🤖 Step mapping bounds attached to agent '{selected_agent}' (attempt {attempts+1})")
            
            # 2. Memory Context Pre-fetch gracefully ensuring prior hallucinations resolve firmly
            past_memories = self.mem_engine.search_memory(node.description, limit=3)
            mem_context = ""
            if past_memories:
                mem_context = "### SEMANTIC MEMORY INJECTION ###\n" + "\n\n".join([m.document for m in past_memories])

            workspace_state = f"Active Sandbox Context Bounds Attached natively.\n{mem_context}"
            if last_error:
                workspace_state += f"\n### PREVIOUS ERROR TRACE (RETRY BOUNDS ACTIVE) ###\n{last_error}"

            def streaming_stub(chunk: str): pass

            # 3. LLM Action Planning gracefully bounding JSON native capabilities exclusively
            res_content = execute_markdown_agent(
                task_type="code",
                step_description=node.description,
                error_or_context=f"Please utilize strictly requested tools: {node.required_tools}",
                workspace_context=workspace_state,
                model="Auto",
                stream_callback=streaming_stub
            )
            
            # 4. Executor Tool Bindings utilizing secure Sandboxes
            parsed = extract_json_payload(res_content)
            if parsed.get("error"):
                last_error = parsed["error"]
                self.log_callback(node.step_id, "error", f"LLM Parsing Trap: {last_error}")
            else:
                exec_result = execute_step(parsed, self.workspace_dir, dry_run=False, fast_mode=True)
                
                if exec_result["success"]:
                    # 5. Success Capture & Deep Memory Store seamlessly bounding Task bounds flawlessly
                    self.mem_engine.store_memory(
                        agent_id=selected_agent,
                        mem_type="task",
                        content=f"TASK:\n{node.description}\n\nSTRATEGY:\n{res_content}"
                    )
                    
                    # Store Error Remediation knowledge explicitly capturing successful patches instantly
                    if last_error:
                        self.mem_engine.store_memory(
                            selected_agent, "error",
                            f"ERROR RESOLUTION CAUGHT:\n{last_error}\n\nHOW IT FIXED:\n{res_content}"
                        )
                    
                    node.output = exec_result["stdout"]
                    node.status = "completed"
                    self.node_callback(node.step_id, "COMPLETED")
                    self.log_callback(node.step_id, "result", f"✓ Node {node.step_id} succeeded.")
                    console.print(f"[bold green]✓ Node {node.step_id} completed.[/bold green]")
                    with self.lock:
                        self.total_nodes_executed += 1
                    return True
                else:
                    last_error = exec_result["stderr"]
                    self.log_callback(node.step_id, "error", f"✗ Node {node.step_id} Sandbox rejection limit reached:\n{last_error}")

            attempts += 1
            node.retries += 1
            node.error = last_error

        # Failures must seamlessly catch cleanly trapping graph blockages reliably.
        node.status = "failed"
        self.node_callback(node.step_id, "FAILED")
        console.print(f"[bold red]✗ Terminating topological resolution trapping recursion on Node {node.step_id}[/bold red]")
        return False


    def run_graph(self, graph: "TaskGraph") -> int:  # noqa: F821
        """Execute the DAG respecting constraint bounds. Returns total nodes executed."""
        self.start_ts = time.time()  # Reset at execution time, not construction time
        console.print("[bold magenta]➜ [DAG ORCHESTRATOR] Starting execution...[/bold magenta]")
        futures_map: dict = {}
        
        # Continuously monitor concurrent queue mapping independent tasks rapidly seamlessly
        while not graph.is_complete():
            if time.time() - self.start_ts > self.max_time:
                console.print("[bold red]✗ Constraint Engine: max_time exceeded natively locking graph.[/bold red]")
                break
            if self.total_nodes_executed >= self.max_steps:
                console.print("[bold red]✗ Constraint Engine: max_steps limit reached halting cleanly.[/bold red]")
                break
                
            executable = graph.get_executable_nodes()
            
            if not executable and not futures_map:
                if graph.has_failures():
                    console.print("[bold red]✗ Pipeline blocked violently by recursive DAG Node explicit failures.[/bold red]")
                else:
                    console.print("[bold red]✗ Infinite DAG loop resolution trap active natively avoiding execution lockups.[/bold red]")
                break
                
            for node in executable:
                if self.total_nodes_executed >= self.max_steps:
                    console.print("[bold red]✗ Constraint Engine: max_steps limit reached halting cleanly.[/bold red]")
                    break
                node.status = "running"
                self.node_callback(node.step_id, "RUNNING")
                future = self.pool.submit(self._execute_step, node)
                futures_map[future] = node
                
            if futures_map:
                done, _ = concurrent.futures.wait(
                    futures_map.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                for f in done:
                    node = futures_map.pop(f)
                    if f.exception():
                        logger.error(
                            "Node %s raised an unhandled exception: %s",
                            node.step_id,
                            f.exception(),
                        )
                        node.status = "failed"
                        self.node_callback(node.step_id, "FAILED")
                    
        self.pool.shutdown(wait=True)
        return self.total_nodes_executed
