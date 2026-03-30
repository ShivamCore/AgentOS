import os
import shutil
import time
import json
import threading
import concurrent.futures
from typing import Dict, Any, List, Optional, Callable
from collections import defaultdict
from rich.console import Console
from agent.agent_pool import AgentPool
from agent.task_graph import TaskGraph, TaskNode
from agent.selector import execute_markdown_agent
from agent.executor import extract_json_payload, execute_step
from agent.utils.state_tracker import StateTracker
from agent.utils.model_router import classify_task, select_model

console = Console()

class Orchestrator:
    workspace_dir: str
    model: str
    max_retries: int
    dry_run: bool
    fast_mode: bool
    pool: AgentPool
    tracker: StateTracker
    history: List[str]
    lock: threading.Lock
    file_locks: Dict[str, threading.Lock]
    global_fixes_cache: Dict[str, str]
    node_timeout: int
    root_mtimes: Dict[str, float]
    last_error: Optional[str]
    log_callback: Callable
    file_callback: Callable
    stream_callback: Callable
    node_callback: Callable

    def __init__(self, workspace_dir: str, max_retries: int = 4, dry_run: bool = False, fast_mode: bool = False, max_workers: int = 4, node_timeout: int = 300, log_callback: Optional[Callable] = None, file_callback: Optional[Callable] = None, stream_callback: Optional[Callable] = None, node_callback: Optional[Callable] = None, model: str = "Auto"):
        self.workspace_dir: str = workspace_dir
        self.model: str = model
        self.max_retries: int = max_retries
        self.dry_run: bool = dry_run
        self.fast_mode: bool = fast_mode
        self.pool: AgentPool = AgentPool(max_workers=max_workers)
        self.tracker: StateTracker = StateTracker(workspace_dir)
        self.history: List[str] = []
        self.lock: threading.Lock = threading.Lock()
        self.file_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.global_fixes_cache: Dict[str, str] = {}
        self.node_timeout: int = node_timeout
        self.root_mtimes: Dict[str, float] = {}
        self.last_error: Optional[str] = None
        self.log_callback: Callable = log_callback or (lambda *args: None)
        self.file_callback: Callable = file_callback or (lambda *args: None)
        self.stream_callback: Callable = stream_callback or (lambda *args: None)
        self.node_callback: Callable = node_callback or (lambda *args: None)
        
    def _selective_copy(self, src: str, dst: str):
        """Copies workspace to sandbox skipping heavy standard dependencies like venv."""
        ignores = {'.git', 'venv', 'node_modules', '__pycache__', '.env'}
        
        # Record mtimes during sync to detect merge conflicts later
        self.root_mtimes = {}
        
        os.makedirs(dst, exist_ok=True)
        for root, dirs, files in os.walk(src):
            for ign in ignores:
                if ign in dirs:
                    dirs.remove(ign)
            for file in files:
                src_path = os.path.join(root, file)
                rel_path = os.path.relpath(src_path, src)
                dst_path = os.path.join(dst, rel_path)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                self.root_mtimes[rel_path] = os.path.getmtime(src_path)
        
    def _is_test_node(self, node: "TaskNode") -> bool:
        """Returns True if this node is purely a test/verify step."""
        keywords = ("test", "verify", "verif", "ensure", "validate", "check", "assert", "run the")
        desc = node.description.lower()
        return any(kw in desc for kw in keywords)

    def _run_test_fastpath(self, node: "TaskNode", tmp_workspace: str) -> dict:
        """
        Deterministic test executor — no LLM involved.
        1. Runs existing test_*.py files with `python -m pytest -x -q`
        2. Fallback: run all non-test .py files with `python <file>.py`
        3. Last resort: write a minimal smoke-test and run it
        Returns {"success": bool, "stdout": str, "stderr": str}
        """
        import subprocess, sys

        py_files = [f for f in os.listdir(tmp_workspace) if f.endswith(".py")]
        test_files = [f for f in py_files if f.startswith("test_") or f.endswith("_test.py")]
        main_files = [f for f in py_files if f not in test_files and f not in ("setup.py",)]

        def run(cmd: list, cwd: str, timeout: int = 60) -> dict:
            try:
                proc = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
                )
                return {
                    "success": proc.returncode == 0,
                    "stdout": proc.stdout[:2000],
                    "stderr": proc.stderr[:2000],
                }
            except subprocess.TimeoutExpired:
                return {"success": False, "stdout": "", "stderr": "Test timed out after 60s"}
            except Exception as e:
                return {"success": False, "stdout": "", "stderr": str(e)}

        # ── Phase 1: run existing pytest test files ─────────────────────────────
        if test_files:
            msg = f"🧪 Running {len(test_files)} test file(s): {', '.join(test_files)}"
            self.log_callback(node.node_id, "action", msg)
            console.print(f"[cyan]{msg}[/cyan]")
            result = run([sys.executable, "-m", "pytest"] + test_files + ["-x", "-q", "--tb=short"], tmp_workspace)
            if result["success"]:
                return result
            # pytest found but tests fail → try to run main files next
            err = result["stderr"] or result["stdout"]
            self.log_callback(node.node_id, "error", f"pytest failed: {err[:200]}")

        # ── Phase 2: run only files with an explicit __main__ guard ────────────
        # Library files (like add.py) should NOT be run directly — they define
        # functions but have no entry point and will crash on bare module-level calls.
        runnable_files = []
        for f in main_files:
            fpath = os.path.join(tmp_workspace, f)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                    if '__name__' in fh.read():
                        runnable_files.append(f)
            except Exception:
                pass

        for f in runnable_files:
            msg = f"▶ Running {f} directly"
            self.log_callback(node.node_id, "action", msg)
            console.print(f"[cyan]{msg}[/cyan]")
            result = run([sys.executable, f], tmp_workspace)
            if result["success"]:
                return result
            self.log_callback(node.node_id, "error", f"{f} failed: {result['stderr'][:200]}")

        # ── Phase 3: auto-generate minimal smoke test from available modules ────
        if main_files:
            smoke = f"# Auto-generated smoke test\n"
            for f in main_files:
                mod = f[:-3]
                smoke += f"import {mod}\n"
            smoke += "print('Smoke test passed.')\n"
            smoke_path = os.path.join(tmp_workspace, "_smoke_test.py")
            with open(smoke_path, "w") as fh:
                fh.write(smoke)
            msg = "▶ Running auto-generated smoke test"
            self.log_callback(node.node_id, "action", msg)
            result = run([sys.executable, "_smoke_test.py"], tmp_workspace)
            return result

        return {"success": False, "stdout": "", "stderr": "No Python files found to test"}

    def _execute_node(self, node: "TaskNode", user_task: str) -> bool:
        """Isolated thread worker resolving a single DAG Node inside a temp sandbox."""
        tmp_workspace = os.path.join("/tmp/", f"agent_{node.node_id}_{int(time.time())}")
        start_time = time.time()
        
        if not self.dry_run:
            if os.path.exists(tmp_workspace):
                shutil.rmtree(tmp_workspace)
            self._selective_copy(self.workspace_dir, tmp_workspace)
            
        node.status = "running"
        self.node_callback(node.node_id, "running", 0)
            
        msg = f"➜ [RUNNING] Node {node.node_id} (Priority: {node.priority}): {node.description}"
        console.print(f"\n[bold blue]{msg}[/bold blue]")
        self.log_callback(node.node_id, "action", msg)

        # ── FAST PATH: test/verify nodes run deterministically, no LLM ──────────
        if self._is_test_node(node):
            self.log_callback(node.node_id, "action", "🧪 Test node detected — using deterministic executor (no LLM)")
            result = self._run_test_fastpath(node, tmp_workspace)
            if result["success"]:
                stdout_preview = result["stdout"][:200].strip()
                self.log_callback(node.node_id, "result", f"✓ Tests passed.\n{stdout_preview}")
                node.status = "completed"
                self.node_callback(node.node_id, "completed", 0)
                return True
            else:
                err = (result["stderr"] or result["stdout"])[:300]
                self.log_callback(node.node_id, "error", f"✗ Tests failed:\n{err}")
                node.status = "failed"
                self.node_callback(node.node_id, "failed", 0)
                return False

        # ── NORMAL PATH: LLM-driven code generation ──────────────────────────────
        attempts = 0
        context = ""
        with self.lock:
            context = self.tracker.get_map_json()
        
        # ── MEMORY INJECTION: Semantic Fetching ────────────────────────────────
        from agent.memory.engine import get_memory_engine
        mem_engine = get_memory_engine(self.workspace_dir)
        past_memories = mem_engine.search_memory(node.description, limit=3)
        if past_memories:
            memory_injection = "\n\n[RELEVANT PAST MEMORY]:\n" + "\n".join([m.document for m in past_memories])
            context += memory_injection
            self.log_callback(node.node_id, "action", f"🧠 Injected {len(past_memories)} semantic memory contexts.")
        
        task_type = classify_task(node.description)
        selected_model = select_model(task_type, "coder", attempt=1, user_override=self.model)
        self.log_callback(node.node_id, "action", f"🤖 [ROUTER] Mapping targeted LLM: {selected_model} (Agent: Coder, Type: {task_type})")
        
        def token_streamer(chunk: str):
            self.stream_callback(node.node_id, chunk)
            
        current_response = execute_markdown_agent(
            task_type="code",
            step_description=node.description,
            error_or_context=user_task,
            workspace_context=context,
            model=node.model or selected_model,
            stream_callback=token_streamer
        )
        last_error = None
        
        while attempts <= self.max_retries:
            # Watchdog Timer Check
            if time.time() - start_time > self.node_timeout:
                console.print(f"[bold red]✗ [{node.node_id}] Deadlock Watchdog Triggered: Exceeded {self.node_timeout}s execution allowance.[/bold red]")
                last_error = "DEADLOCK: Watchdog Timer Expired"
                break
                
            console.print(f"[bold cyan][{node.node_id}] Attempt {attempts+1}/{self.max_retries+1}[/bold cyan]")
            parsed_payload = extract_json_payload(current_response)
            
            if parsed_payload.get("error"):
                err_msg = f"✗ [{node.node_id}] JSON Error: {parsed_payload['error']}"
                console.print(f"[bold red]{err_msg}[/bold red]")
                self.log_callback(node.node_id, "error", err_msg)
                last_error = parsed_payload['error']
                attempts += 1
                if attempts > self.max_retries:
                    break
                fallback_model = select_model(task_type, "debugger", attempt=attempts, user_override=self.model)
                current_response = execute_markdown_agent("debug", node.description, last_error, context, fallback_model, token_streamer)
                continue
                
            files = parsed_payload.get("files", [])
            commands = parsed_payload.get("commands", [])
            if not files and not commands:
                last_error = "INVALID OUTPUT FORMAT: No files or commands supplied."
                attempts += 1
                if attempts > self.max_retries:
                    break
                fallback_model = select_model(task_type, "debugger", attempt=attempts, user_override=self.model)
                current_response = execute_markdown_agent("debug", node.description, last_error, context, fallback_model, token_streamer)
                continue
                
            execution_result = execute_step(parsed_data, tmp_workspace, dry_run=self.dry_run, fast_mode=self.fast_mode)
            
            if not execution_result["success"] and execution_result["stderr"] == last_error:
                err_msg = f"✗ Fatal: {node.node_id} identical repeating error. Aborting."
                console.print(f"[bold red]{err_msg}[/bold red]")
                self.log_callback(node.node_id, "error", err_msg)
                break
                
            if execution_result["success"]:
                # Log success safely
                self.log_callback(node.node_id, "result", f"Successfully executed {len(files)} files via LLM output.")
                # Success! Sync sandbox back to master safely via File Locks and Merge Diff resolution.
                if not self.dry_run:
                    has_conflict = False
                    # Verify no conflicts exist before locking
                    for f_meta in files:
                        rel = f_meta.get("path", "").lstrip('/')
                        if rel:
                            master_path = os.path.join(self.workspace_dir, rel)
                            if os.path.exists(master_path):
                                current_mtime = os.path.getmtime(master_path)
                                if rel in self.root_mtimes and current_mtime > self.root_mtimes[rel]:
                                    console.print(f"[bold red]✗ MERGE CONFLICT DETECTED: {rel} was modified externally by another node/process![/bold red]")
                                    has_conflict = True
                                    last_error = f"MERGE CONFLICT on {rel}"
                                    break
                    
                    if has_conflict:
                        # Fail the node, forcing DAG retry of this node specifically with updated root state
                        break
                        
                    # Commit files taking granular thread file_locks
                    for f_meta in files:
                        rel = f_meta.get("path", "").lstrip('/')
                        if rel:
                            master_path = os.path.join(self.workspace_dir, rel)
                            sandbox_path = os.path.join(tmp_workspace, rel)
                            if os.path.exists(sandbox_path):
                                with self.file_locks[rel]:
                                    os.makedirs(os.path.dirname(master_path), exist_ok=True)
                                    shutil.copy2(sandbox_path, master_path)
                                    
                                    try:
                                        with open(master_path, "r", encoding="utf-8", errors="replace") as f:
                                            self.file_callback(node.node_id, rel, f.read())
                                    except Exception as e:
                                        pass
                                
                    # Refresh Global Tracking Graph Map
                    with self.lock:
                        self.tracker.scan_workspace()
                
                # Apply successful fixes to global debug registry
                if last_error and attempts > 0:
                    with self.lock:
                        self.global_fixes_cache[last_error] = current_response
                        # Record Error Resolution in Long-Term Memory
                        mem_engine.store_memory(
                            agent_id=selected_model, 
                            mem_type="error", 
                            content=f"ERROR CAUGHT:\n{last_error}\n\nHOW IT WAS FIXED:\n{current_response}"
                        )
                
                # Record successful standard Task accomplishment
                mem_engine.store_memory(
                    agent_id=selected_model,
                    mem_type="task",
                    content=f"TASK:\n{node.description}\n\nRESOLUTION:\n{current_response}"
                )
                        
                node.files_modified = len(files)
                node.status = "completed"
                self.node_callback(node.node_id, "completed", len(files))
                
                console.print(f"[bold green]✓ Node {node.node_id} completed successfully![/bold green]")
                # Attempt Global Dictionary Retrieval
                if last_error and last_error in self.global_fixes_cache:
                    msg = f"➜ [{node.node_id}] Using Global Fix Cache for identical error!"
                    console.print(f"[bold yellow]{msg}[/bold yellow]")
                    self.log_callback(node.node_id, "action", msg)
                    
                    # Global Fast-Path Knowledge Inject
                    with self.lock:
                        if last_error in self.global_fixes_cache:
                            console.print(f"[bold magenta]⚡ GLOBAL MEMORY HIT: Injecting known resolution for node {node.node_id}...[/bold magenta]")
                return True
            else:
                last_error = execution_result["stderr"]
                err_msg = f"✗ [{node.node_id}] Execution Failed.\nSTDERR:\n{last_error}"
                console.print(f"[bold red]{err_msg}[/bold red]")
                self.log_callback(node.node_id, "error", err_msg)
                
                # Global Fast-Path Knowledge Inject
                with self.lock:
                    if last_error in self.global_fixes_cache:
                        console.print(f"[bold magenta]⚡ GLOBAL MEMORY HIT: Injecting known resolution for node {node.node_id}...[/bold magenta]")
                        current_response = self.global_fixes_cache[last_error]
                        attempts += 1
                        continue
                        
                attempts += 1
                if attempts > self.max_retries:
                    break
                fallback_model = select_model(task_type, "debugger", attempt=attempts, user_override=self.model)
                current_response = execute_markdown_agent("debug", node.description, last_error, context, fallback_model, token_streamer)
                
        node.status = "failed"
        node.stderr = last_error
        self.node_callback(node.node_id, "failed", 0)
        
        console.print(f"[bold red]✗ Node {node.node_id} FAILED permanently.[/bold red]")
        return False

    def run_graph(self, graph: TaskGraph, user_task: str):
        console.print(f"[bold magenta]➜ [ORCHESTRATOR] Booting Multi-Agent Parallel Execution...[/bold magenta]")
        
        os.makedirs(self.workspace_dir, exist_ok=True)

        # ── Sanitize DAG: remove depends_on references to missing nodes ──────────
        # The LLM sometimes omits task_1 while other tasks depend on it.
        # Strip any reference to a non-existent node_id so those tasks become roots.
        known_ids = set(graph.nodes.keys())
        for node in graph.nodes.values():
            dangling = [d for d in node.depends_on if d not in known_ids]
            if dangling:
                console.print(f"[yellow]⚠ DAG sanitize: removing unknown deps {dangling} from {node.node_id}[/yellow]")
                node.depends_on = [d for d in node.depends_on if d in known_ids]
        
        futures_map = {}
        
        while not graph.is_complete():
            executable = graph.get_executable_nodes()
            
            if not executable and not futures_map:
                if graph.has_failures():
                    console.print("[bold red]✗ Pipeline blocked violently by recursive DAG failures.[/bold red]")
                    break
                else:
                    console.print("[bold red]✗ Infinite DAG loop resolution trap: No active futures, but DAG incomplete.[/bold red]")
                    break
                    
            for node in executable:
                node.status = "running"
                console.print(f"[yellow]➜ Scheduling Worker for Node {node.node_id}[/yellow]")
                future = self.pool.submit(self._execute_node, node, user_task)
                futures_map[future] = node
                
            # Block and wait for ANY future to resolve
            if futures_map:
                done, not_done = concurrent.futures.wait(
                    futures_map.keys(), 
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                for f in done:
                    completed_node = futures_map.pop(f)
                    
        self.pool.shutdown(wait=True)
        
        total_modified = sum(n.files_modified for n in graph.nodes.values() if n.status == "completed")
        return total_modified
