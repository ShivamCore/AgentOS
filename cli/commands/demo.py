"""
AgentOS CLI — `demo` command
================================
Run a predefined, impressive task optimized for screen recordings and demos.

Usage:
    agentos demo
"""

from __future__ import annotations

import contextlib

import typer

from rich.console import Console

from cli.commands.run import run_task

console = Console()


def run_demo(
    model: str = typer.Option(
        None, "--model", "-m", help="Ollama model override (default: Auto/OLLAMA_MODEL)"
    ),
) -> None:
    """Submit a small predefined task and watch the DAG execute."""

    task_desc = (
        "Create two independent Python modules: mathutils.py with a function add(a, b) and "
        "strutils.py with a function shout(s) that returns s.upper() + '!'. "
        "Then create test_utils.py with pytest tests for both functions."
    )

    console.print()
    console.print("[bold magenta]🎬 AgentOS Interactive Demo[/bold magenta]")
    console.print(
        "[dim]Running a predefined complex task to showcase autonomous execution.[/dim]"
    )
    console.print()

    # We call the core run function but force the parameters to optimize for video
    # run_task raises typer.Exit when the task ends; the demo just returns.
    with contextlib.suppress(typer.Exit):
        run_task(
            task=task_desc,
            title="Two modules and their tests",
            task_type="build_app",
            stack=["Python"],
            feature=[],
            steps=5,
            timeout=300,
            risk="safe",
            model=model,
            json_out=False,
            verbose=False,  # We want the premium Live UI, not raw logs
            silent=False,
            watch=True,  # Guarantee watch mode for maximum visual impact
        )
