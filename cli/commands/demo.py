"""
AgentOS CLI — `demo` command
================================
Run a predefined, impressive task optimized for screen recordings and demos.

Usage:
    agentos demo
"""

from __future__ import annotations

import typer
from rich.console import Console

from cli.commands.run import run_task

console = Console()

def run_demo() -> None:
    """Run an automated, visually impressive task for screen recordings."""
    
    task_desc = (
        "Build a classic Snake game in Python using the built-in curses library. "
        "Requirements: "
        "\n1. The snake should grow when it eats food."
        "\n2. Maintain a visible score counter at the top."
        "\n3. The game should speed up slightly as the snake grows."
        "\n4. If the snake hits the wall or itself, display a 'Game Over' message and exit cleanly."
        "\nEnsure the code is robust, fully typed, and cleanly formatted."
    )
    
    console.print()
    console.print("[bold magenta]🎬 AgentOS Interactive Demo[/bold magenta]")
    console.print("[dim]Running a predefined complex task to showcase autonomous execution.[/dim]")
    console.print()
    
    # We call the core run function but force the parameters to optimize for video
    try:
        run_task(
            task=task_desc,
            title="Terminal Snake Game",
            task_type="build_app",
            stack=["Python", "curses"],
            feature=["Score Tracking", "Progressive Difficulty"],
            steps=7,
            timeout=300,
            risk="safe",
            model=None, # Will use config default Auto
            json_out=False,
            verbose=False, # We want the premium Live UI, not raw logs
            silent=False,
            watch=True, # Guarantee watch mode for maximum visual impact
        )
    except typer.Exit:
        # Ignore exits to cleanly terminate
        pass
