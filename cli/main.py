"""
AgentOS CLI — main entrypoint
==============================
Registered as the `agentos` console script via pyproject.toml.

    agentos run "Build a Stripe webhook handler"
    agentos status <task_id>
    agentos list
    agentos result <task_id>
    agentos retry <task_id>
    agentos config
    agentos config --set api_url=http://myserver:8000
"""

from __future__ import annotations

import typer
from rich.console import Console

from cli.commands.config import show_config
from cli.commands.list_tasks import list_tasks
from cli.commands.result import result, retry
from cli.commands.run import run_task
from cli.commands.status import status
from cli.commands.explain import explain_task
from cli.commands.doctor import run_doctor
from cli.commands.demo import run_demo

console = Console()

app = typer.Typer(
    name="agentos",
    help="⚡ AgentOS — Local-first autonomous agent execution system.",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=True,
)

# ── Register commands ─────────────────────────────────────────────────────────
app.command("run", help="Submit a task and stream live progress.")(run_task)
app.command("status", help="Check status and execution graph of a task.")(status)
app.command("list", help="List recent tasks.")(list_tasks)
app.command("result", help="Show structured result of a completed task.")(result)
app.command("retry", help="Re-queue a failed task.")(retry)
app.command("config", help="Show configuration and check API health.")(show_config)
app.command("explain", help="Show the AI's planner reasoning and tool execution logic.")(explain_task)
app.command("doctor", help="Run system diagnostics.")(run_doctor)
app.command("demo", help="Run a predefined, impressive task optimized for screen recordings.")(run_demo)


@app.command("version")
def version() -> None:
    """Print AgentOS CLI version."""
    console.print("[bold]AgentOS CLI[/bold]  v1.0.0")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
