import os
from rich.console import Console
from agent.planner import plan_task
from agent.orchestrator import Orchestrator
from agent.utils.state_tracker import StateTracker

console = Console()

class AutonomousAgent:
    def __init__(self, workspace_dir: str, max_retries: int = 4, dry_run: bool = False, fast_mode: bool = False) -> None:
        self.workspace_dir = workspace_dir
        self.max_retries = max_retries
        self.dry_run = dry_run
        self.fast_mode = fast_mode
        self.history = []
        self.last_error = None
        self.tracker = StateTracker(workspace_dir)
        
    def _compress_error(self, text: str, max_lines: int = 15) -> str:
        """Aggressive compression: strictly returns the last N lines of a stack trace."""
        if not text:
            return ""
        lines = text.strip().split("\n")
        if len(lines) <= max_lines:
            return text
        return f"... [{len(lines) - max_lines} lines omitted] ...\n" + "\n".join(lines[-max_lines:])
        
    def _truncate_log(self, text: str, max_len: int = 1500) -> str:
        """Intelligently truncates log strings to keep them under prompt limits."""
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        half = max_len // 2
        return text[:half] + f"\n... [{len(text) - max_len} chars truncated] ...\n" + text[-half:]
        
    def _classify_error(self, stderr: str) -> str:
        """Simple heuristic to classify common execution errors."""
        err_lower = stderr.lower()
        if "syntaxerror" in err_lower or "indentationerror" in err_lower:
            return "Syntax/Formatting Error"
        if "modulenotfound" in err_lower or "importerror" in err_lower:
            return "Missing Dependency/Import Error"
        if "typeerror" in err_lower or "valueerror" in err_lower or "attributeerror" in err_lower:
            return "Runtime Type/Value Error"
        if "assertionerror" in err_lower or "failure in" in err_lower:
            return "Test/Assertion Failure"
        return "General Runtime Error"
        
    def run(self, user_task: str):
        console.print(f"[bold blue]Starting Task:[/bold blue] {user_task}")
        console.print(f"[bold blue]Workspace:[/bold blue] {self.workspace_dir}")
        if self.dry_run:
            console.print("[bold yellow][! DRY RUN MODE ENABLED !][/bold yellow]")
        console.print("")
        
        # Ensure workspace exists
        os.makedirs(self.workspace_dir, exist_ok=True)
        
        # 1. Planning
        console.print("[bold green]➜ [PLANNING] Building atomic DAG steps...[/bold green]")
        task_graph = plan_task(user_task)
        
        for node in task_graph.nodes.values():
            console.print(f"  - [{node.node_id}] {node.description} (Deps: {node.depends_on})")
            
        formatted_plan = "\n".join([f"[{n.node_id}] {n.description}" for n in task_graph.nodes.values()])
        self.history.append(f"[PLAN - DAG]\n{formatted_plan}")
        
        # 2. Orchestration Loop
        orchestrator = Orchestrator(
            workspace_dir=self.workspace_dir,
            max_retries=self.max_retries,
            dry_run=self.dry_run,
            fast_mode=self.fast_mode,
            max_workers=4
        )
        total_files_modified = orchestrator.run_graph(task_graph, user_task)
        
        # Sync final history pointer
        self.last_error = orchestrator.last_error if hasattr(orchestrator, "last_error") else None
                
        # 3. Persistent Task Memory
        import json
        history_file = os.path.join(self.workspace_dir, "task_history.json")
        try:
            task_cache = []
            if os.path.exists(history_file):
                with open(history_file, 'r', encoding='utf-8') as f:
                    task_cache = json.load(f)
            task_cache.append({
                "task": user_task,
                "success": total_files_modified > 0,
                "fast_mode": self.fast_mode,
                "last_error": self.last_error
            })
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(task_cache, f, indent=2)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not save persistent task history: {e}[/yellow]")
            
        if total_files_modified > 0:
            console.print("\n[bold magenta]🎉 Task Completed Successfully! 🎉[/bold magenta]")
        else:
            console.print("\n[bold red]✗ Task Failed. No actual files were generated or modified.[/bold red]")


