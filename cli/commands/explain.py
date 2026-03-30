"""
AgentOS CLI — `explain` command
================================
Explain planner reasoning, agent selection, and tool execution for a given task.

Usage:
    agentos explain <task_id>
"""

from __future__ import annotations

import json
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli import client
from cli.client import APIError

console = Console()

def explain_task(task_id: str = typer.Argument(..., help="Task ID to explain.")) -> None:
    """Show the AI's internal reasoning (planning, routing, tool usage)."""
    try:
        data = client.get_task_explain(task_id)
    except APIError as e:
        console.print(f"[red]✗ {e.detail}[/red]")
        raise typer.Exit(code=1)

    planner_reasoning = data.get("planner_reasoning", [])
    agent_selection = data.get("agent_selection", [])
    tool_decisions = data.get("tool_usage_decisions", [])

    console.print()
    console.print(f"[bold blue]🧠 AI Explainability Report[/bold blue]  [dim]{task_id}[/dim]")
    console.print()

    # 1. Planner Reasoning
    p_text = []
    if planner_reasoning:
        for r in planner_reasoning:
            p_text.append(f"  [dim]·[/dim] {r}")
    else:
        p_text.append("  [dim]No planner output recorded yet.[/dim]")

    console.print(Panel("\n".join(p_text), title="[bold cyan]1. Task Planning[/bold cyan]", border_style="cyan", expand=False))

    # 2. Agent Routing decisions
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    t.add_column("Agent Selected")
    t.add_column("Confidence", justify="right")
    t.add_column("Reasoning", style="dim")
    
    for a in agent_selection:
        conf = a.get("confidence", 0)
        c_str = f"[green]{conf}%[/green]" if conf >= 80 else f"[yellow]{conf}%[/yellow]"
        t.add_row(
            a.get("agent", "unknown"),
            c_str,
            a.get("reason", "")
        )
    
    if not agent_selection:
        t.add_row("[dim]None[/dim]", "", "")

    console.print("\n[bold magenta]2. Agent Selection Routing[/bold magenta]")
    console.print(t)
    
    # 3. Tool Usage (Nodes)
    n_table = Table(show_header=False, box=None, padding=(0, 2))
    n_table.add_column()
    n_table.add_column()
    for n in tool_decisions:
        n_table.add_row(f"[dim]{n.get('node', '?')}[/dim]", n.get("goal", ""))
        
    console.print("\n[bold yellow]3. Executed Steps (Tools context)[/bold yellow]")
    if tool_decisions:
        console.print(n_table)
    else:
        console.print("  [dim]No steps recorded.[/dim]")
    
    console.print()
