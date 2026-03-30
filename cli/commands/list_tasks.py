"""
AgentOS CLI — `list` command
==============================
Show recent tasks in a clean table.

Usage:
    agentos list
    agentos list --limit 5
    agentos list --status FAILED
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
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


def _truncate(s: str, n: int = 60) -> str:
    return (s[:n] + "…") if len(s) > n else s


def list_tasks(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of tasks to show (max 50)."),
    filter_status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status: COMPLETED | FAILED | RUNNING"),
) -> None:
    """List the most recent AgentOS tasks."""

    try:
        tasks = client.list_tasks(limit=min(limit, 50))
    except APIError as e:
        console.print(f"[red]✗ {e.detail}[/red]")
        raise typer.Exit(code=1)

    if filter_status:
        tasks = [t for t in tasks if t.get("status", "").upper() == filter_status.upper()]

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("Task ID", style="dim", min_width=36)
    table.add_column("Title / Description", min_width=40)
    table.add_column("Status", min_width=16)
    table.add_column("Created", min_width=20)

    for t in tasks:
        task_status = t.get("status", "UNKNOWN")
        color = _STATUS_COLOR.get(task_status, "white")
        title = t.get("title") or _truncate(t.get("description", ""), 60)
        created = str(t.get("created_at", ""))[:19]  # trim microseconds

        table.add_row(
            t.get("id", ""),
            title,
            Text(task_status, style=color),
            Text(created, style="dim"),
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(tasks)} task(s) shown. Use [cyan]agentos status <task_id>[/cyan] for details.[/dim]")
    console.print()
