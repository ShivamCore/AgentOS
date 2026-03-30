"""
AgentOS CLI — `result` command
================================
Fetch and display the structured result for a completed task.

Usage:
    agentos result <task_id>
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from cli import client
from cli.client import APIError

console = Console()


def _display_result(task_id: str, json_out: bool = False) -> None:
    import json as _json

    try:
        data = client.get_task_result(task_id)
    except APIError as e:
        console.print(f"[red]✗ {e.detail}[/red]")
        raise typer.Exit(code=1)

    if json_out:
        console.print(Syntax(_json.dumps(data, indent=2), "json", theme="monokai"))
        return

    summary = data.get("summary", "")
    files = data.get("files_modified", [])
    errors = data.get("errors", [])
    next_steps = data.get("next_steps", [])

    # High-impact heuristic confidence score
    confidence = max(0, min(100, 100 - (len(errors) * 15) - (0 if files else 10)))
    if summary and "failed" in summary.lower():
        confidence = min(confidence, 35)

    c_color = "green" if confidence >= 80 else "yellow" if confidence >= 50 else "red"

    lines = []
    if summary:
        lines.append(f"[bold]Summary[/bold]\n  {summary}\n  Confidence: [{c_color}]{confidence}%[/{c_color}]")
    if files:
        lines.append("\n[bold]Files modified[/bold]")
        for f in files:
            lines.append(f"  [green]+[/green] {f}")
    if errors:
        lines.append("\n[bold]Errors (Action Required)[/bold]")
        for e in errors:
            lines.append(f"  [red]✗[/red] {e}")
    if next_steps:
        lines.append("\n[bold]Suggested next steps[/bold]")
        for i, ns in enumerate(next_steps, 1):
            lines.append(f"  {i}. {ns}")

    if not lines:
        console.print("[dim]No result data for this task yet.[/dim]")
        return

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title=f"[bold green]Result Output[/bold green]  [dim]{task_id}[/dim]",
        border_style="green",
    ))
    console.print()


def result(
    task_id: str = typer.Argument(..., help="Task ID to fetch result for."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON instead of formatted output."),
) -> None:
    """Display the structured result of a completed task."""
    _display_result(task_id, json_out)


def retry(
    task_id: str = typer.Argument(..., help="Task ID to retry."),
) -> None:
    """Re-queue a failed task."""

    try:
        resp = client.retry_task(task_id)
    except APIError as e:
        console.print(f"[red]✗ {e.detail}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]✓ Task re-queued[/green]  [dim]{resp.get('task_id', task_id)}[/dim]")
    console.print(f"  Track: [cyan]agentos status {task_id}[/cyan]")
    console.print()
