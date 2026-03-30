"""
AgentOS CLI — `status` command
================================
Check the current status of a task and display its execution graph.

Usage:
    agentos status <task_id>
    agentos status <task_id> --logs        # also print execution logs
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli import client
from cli.client import APIError

console = Console()

_STATUS_COLOR = {
    "CREATED": "dim",
    "PLANNED": "cyan",
    "RUNNING": "yellow",
    "COMPLETED": "green",
    "PARTIAL_SUCCESS": "yellow",
    "FAILED": "red",
}
_NODE_ICON = {
    "CREATED": "○",
    "RUNNING": "◉",
    "COMPLETED": "✓",
    "FAILED": "✗",
}


def status(
    task_id: str = typer.Argument(..., help="Task ID returned by `agentos run`."),
    logs: bool = typer.Option(False, "--logs", "-l", help="Also print execution logs for each step."),
    result: bool = typer.Option(False, "--result", "-r", help="Also print the structured result summary."),
) -> None:
    """Check the status and execution graph of a task."""

    try:
        detail = client.get_task(task_id)
    except APIError as e:
        console.print(f"[red]✗ {e.detail}[/red]")
        raise typer.Exit(code=1)

    current_status: str = detail.get("status", "UNKNOWN")
    nodes: list[dict] = detail.get("nodes", [])
    title: str = detail.get("title") or detail.get("description", "")[:60]
    color = _STATUS_COLOR.get(current_status, "white")

    # ── Node table ────────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("", width=2)
    table.add_column("Step", style="dim", min_width=4)
    table.add_column("Description", min_width=40)
    table.add_column("Status", min_width=12)
    table.add_column("Files", justify="right", min_width=5)

    for n in nodes:
        ns = n.get("status", "CREATED")
        icon = _NODE_ICON.get(ns, "·")
        nc = _STATUS_COLOR.get(ns, "white")
        table.add_row(
            Text(icon, style=nc),
            Text(n.get("node_id", "?"), style="dim"),
            n.get("description", ""),
            Text(ns, style=nc),
            str(n.get("files_modified", 0)),
        )

    console.print()
    console.print(Panel(
        table if nodes else Text("No steps yet — task may still be queued.", style="dim"),
        title=f"[{color}]{current_status}[/{color}]  [bold]{title}[/bold]  [dim]{task_id}[/dim]",
        border_style=color,
    ))

    # ── Execution logs ────────────────────────────────────────────────────────
    if logs:
        try:
            log_data = client.get_task_logs(task_id)
            if log_data:
                console.print("\n[bold]Execution Logs[/bold]")
                for log in log_data:
                    content = (log.get("content") or "").strip()
                    if not content:
                        continue
                    log_type = log.get("type", "")
                    node = log.get("node_id", "")
                    prefix = f"[dim][{node}][/dim] " if node else ""
                    style = "red" if log_type == "error" else "dim"
                    console.print(f"  {prefix}[{style}]{content}[/{style}]")
            else:
                console.print("\n[dim]No logs yet.[/dim]")
        except APIError as e:
            console.print(f"[red]Could not fetch logs: {e.detail}[/red]")

    # ── Result summary ────────────────────────────────────────────────────────
    if result and current_status in {"COMPLETED", "PARTIAL_SUCCESS"}:
        try:
            r = client.get_task_result(task_id)
            summary = r.get("summary", "")
            files = r.get("files_modified", [])
            errors = r.get("errors", [])
            next_steps = r.get("next_steps", [])

            console.print("\n[bold]Result[/bold]")
            if summary:
                console.print(f"  {summary}")
            if files:
                console.print("\n[bold]Files modified[/bold]")
                for f in files:
                    console.print(f"  [green]+[/green] {f}")
            if errors:
                console.print("\n[bold]Errors[/bold]")
                for e in errors:
                    console.print(f"  [red]-[/red] {e}")
            if next_steps:
                console.print("\n[bold]Next steps[/bold]")
                for i, ns in enumerate(next_steps, 1):
                    console.print(f"  {i}. {ns}")
        except APIError:
            console.print("[dim]Result not available yet.[/dim]")

    console.print()
