"""
AgentOS CLI — `run` command
============================
Submit a task and stream live output until COMPLETED or FAILED.

Usage:
    agentos run "Build a Stripe webhook handler"
    agentos run "Fix the auth bug" --type fix_bug --steps 3
    agentos run "Add Redis caching" --stack Python Redis --feature "TTL support"
"""

from __future__ import annotations

import json
import time
from typing import Optional

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from cli import client
from cli.client import APIError
from cli.config import cfg

console = Console()

_DONE = {"COMPLETED", "FAILED", "PARTIAL_SUCCESS"}


def _build_live_panel(
    status: str, 
    nodes: list[dict], 
    logs: list[dict], 
    model: str, 
    silent: bool = False, 
    watch: bool = False,
    step_start_times: dict[str, float] | None = None
) -> Panel:
    """Builds the premium live-updating display panel using Rich."""
    lines = []

    # Overall Status Header
    if status == "CREATED":
        lines.append(Text.assemble(("⏳ Submitted...", "dim")))
    elif status == "PLANNED":
        lines.append(Text.assemble(("🧠 Planned DAG...", "cyan")))
    elif status == "RUNNING":
        lines.append(Text.assemble(("⚙️ Executing...", "yellow"), (f"  Model: {model}", "dim")))
    elif status == "COMPLETED":
        lines.append(Text.assemble(("✅ Task Completed", "bold green")))
    elif status == "FAILED":
        lines.append(Text.assemble(("❌ Task Failed", "bold red")))
    else:
        lines.append(Text(f"Status: {status}"))
    
    lines.append(Text(""))

    # Nodes / Steps Display
    total = max(len(nodes), 1)
    for i, node in enumerate(nodes, 1):
        n_status = node.get("status", "CREATED")
        desc = node.get("description", "Unknown step")
        nid = node.get("node_id", "")
        
        if n_status == "COMPLETED":
            lines.append(Text.assemble((f"  ✓ Step {i}/{total}: ", "green"), (desc, "dim")))
        elif n_status == "FAILED":
            lines.append(Text.assemble((f"  ✗ Step {i}/{total}: ", "red"), (desc, "red")))
        elif n_status == "RUNNING":
            eta_str = ""
            if step_start_times and nid in step_start_times:
                elapsed = time.time() - step_start_times[nid]
                rem = max(1, 15 - elapsed)
                eta_str = f" (≈ {rem:.0f}s remaining)"
            lines.append(Text.assemble((f"  ⚙️ Step {i}/{total}: ", "yellow"), (desc, "bold"), (eta_str, "dim")))
        else:
            if watch:
                lines.append(Text.assemble((f"  ○ Step {i}/{total}: ", "dim"), (desc, "dim")))
            
    if not silent and status == "RUNNING":
        lines.append(Text(""))
        # Grab the last relevant log actions to show what the agent is currently doing
        recent_logs = [l for l in logs if l.get("type") in ("action", "error", "result")][-3:]
        for log in recent_logs:
            l_type = log.get("type", "")
            content = (log.get("content") or "").strip().split("\n")[0] # keep it one line
            if len(content) > 80:
                content = content[:77] + "..."
            
            icon = "🛠 " if l_type == "action" else "⚠️ " if l_type == "error" else "💡"
            color = "cyan" if l_type == "action" else "red" if l_type == "error" else "dim"
            lines.append(Text.assemble((f"    {icon} {content}", color)))

    return Panel(
        Group(*lines),
        title="[bold blue]⚡ AgentOS Execution[/bold blue]",
        border_style="blue",
        expand=False,
    )


def run_task(
    task: str = typer.Argument(..., help="Task description — what you want AgentOS to build or fix."),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Short task title (defaults to first 60 chars of task)."),
    task_type: str = typer.Option("build_app", "--type", help="Task type: build_app | fix_bug | refactor_code | create_api"),
    stack: Optional[list[str]] = typer.Option(None, "--stack", "-s", help="Tech stack (repeatable): --stack Python --stack FastAPI"),
    feature: Optional[list[str]] = typer.Option(None, "--feature", "-f", help="Features to include (repeatable)."),
    steps: int = typer.Option(10, "--steps", help="Maximum DAG steps (1-50)."),
    timeout: int = typer.Option(300, "--timeout", help="Hard timeout in seconds."),
    risk: str = typer.Option("balanced", "--risk", help="Risk level: safe | balanced | aggressive"),
    model: str = typer.Option(None, "--model", "-m", help="Ollama model override (default: Auto)"),
    json_out: bool = typer.Option(False, "--json", help="Dump raw JSON continuously."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream all raw full logs bypassing the pretty UI."),
    silent: bool = typer.Option(False, "--silent", help="Suppress all output except the final result JSON."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live stream mode — show all DAG nodes continuously updating."),
) -> None:
    """Submit a task and stream live progress until it completes."""

    _title = title or task[:60]
    _model = model or cfg.default_model
    _stack = list(stack) if stack else []
    _features = list(feature) if feature else []

    valid_types = {"build_app", "fix_bug", "refactor_code", "create_api"}
    if task_type not in valid_types:
        if not silent and not json_out:
            console.print(f"[red]✗ Invalid --type '{task_type}'. Must be one of: {', '.join(sorted(valid_types))}[/red]")
        raise typer.Exit(code=1)

    if not silent and not json_out and not verbose:
        console.print()
        console.print(Panel(
            f"[bold]{_title}[/bold]\n[dim]{task}[/dim]",
            title="[bold blue]⚡ AgentOS Task[/bold blue]",
            border_style="blue",
            expand=False,
        ))
        # ── INSTANT FEEDBACK ──
        console.print()
        console.print("  [dim]🧠 Initializing AgentOS...[/dim]")
        console.print("  [dim]🔍 Loading configuration...[/dim]")
        console.print("  [dim]⚡ Connecting to local runtime...[/dim]")
        console.print()

    try:
        resp = client.create_task(
            title=_title,
            description=task,
            task_type=task_type,
            tech_stack=_stack,
            features=_features,
            max_steps=steps,
            max_time=timeout,
            risk_level=risk,
            model=_model,
        )
    except APIError as e:
        if not silent:
            console.print(f"[red]✗ Failed to create task: {e.detail}[/red]")
        raise typer.Exit(code=1)

    task_id: str = resp["task_id"]
    
    if json_out:
        print(json.dumps({"event": "task_created", "task_id": task_id}))

    current_status = "CREATED"
    seen_logs: set[int] = set()
    step_start_times: dict[str, float] = {}

    # ── Interactive Live Stream (Premium UI) ──
    if not silent and not verbose and not json_out:
        with Live(_build_live_panel(current_status, [], [], _model, silent, watch, step_start_times), console=console, refresh_per_second=4) as live:
            while current_status not in _DONE:
                time.sleep(1.0) # slightly faster polling updates
                try:
                    detail = client.get_task(task_id)
                    logs = client.get_task_logs(task_id)
                except Exception:
                    continue  # network blip, just continue polling
                
                current_status = detail.get("status", "CREATED")
                nodes = detail.get("nodes", [])
                
                # Update ETA trackers
                for n in nodes:
                    nid = n.get("node_id")
                    if n.get("status") == "RUNNING" and nid not in step_start_times:
                        step_start_times[nid] = time.time()
                
                live.update(_build_live_panel(current_status, nodes, logs, _model, silent, watch, step_start_times))

    # ── Verbose Raw Logger Stream ──
    elif verbose and not silent and not json_out:
        console.print(f"[dim]Task {task_id} submitted. Streaming verbose logs...[/dim]\n")
        while current_status not in _DONE:
            time.sleep(cfg.poll_interval)
            try:
                detail = client.get_task(task_id)
                logs = client.get_task_logs(task_id)
                current_status = detail.get("status", "CREATED")
                
                for log in logs:
                    l_id = log.get("id")
                    if l_id not in seen_logs:
                        seen_logs.add(l_id)
                        node = log.get("node_id", "")
                        prefix = f"[dim][{node}][/dim] " if node else ""
                        l_type = log.get("type", "")
                        style = "red" if l_type == "error" else "cyan" if l_type == "action" else "dim"
                        console.print(f"{prefix}[{style}]{log.get('content', '').strip()}[/{style}]")
            except Exception:
                pass

    # ── JSON Poller Stream ──
    elif json_out:
        while current_status not in _DONE:
            time.sleep(cfg.poll_interval)
            try:
                detail = client.get_task(task_id)
                current_status = detail.get("status", "CREATED")
                print(json.dumps({"event": "status_update", "status": current_status}))
            except Exception:
                pass

    # ── Silent Poller ──
    elif silent:
        while current_status not in _DONE:
            time.sleep(cfg.poll_interval)
            try:
                detail = client.get_task(task_id)
                current_status = detail.get("status", "CREATED")
            except Exception:
                pass

    # ── Final Output ────────────────────────────────────────────────────────
    if not json_out and not silent:
        # Call the result command natively to display the structured summary
        from cli.commands.result import _display_result
        _display_result(task_id, json_out=False)

    if silent:
        from cli.commands.result import _display_result
        _display_result(task_id, json_out=True) # Always JSON if silent

    if current_status == "FAILED":
        raise typer.Exit(code=1)
