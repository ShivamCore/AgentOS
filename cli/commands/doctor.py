"""
AgentOS CLI — `doctor` command
================================
Run system diagnostics to ensure AgentOS can operate fully.

Usage:
    agentos doctor
"""

from __future__ import annotations

import sys

from rich.console import Console

from cli import client
from cli.client import APIError
from cli.config import cfg

console = Console()


def run_doctor() -> None:
    """Run full system diagnostics and print a checklist."""
    console.print()
    console.print("[bold blue]🩺 AgentOS System Diagnostics[/bold blue]")
    console.print()

    # 1. Python version
    py_ver = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    console.print(f"  [green]✓[/green] Python    [dim]v{py_ver} (>= 3.11)[/dim]")

    # 2. Sandbox (fail-closed: without it, steps that run commands fail)
    from agent.sandbox import sandbox_status

    sb = sandbox_status()
    if sb["backend"] == "docker":
        console.print(f"  [green]✓[/green] Sandbox   [dim]{sb['detail']}[/dim]")
    elif sb["available"]:
        console.print(
            f"  [yellow]![/yellow] Sandbox   [dim]{sb['detail']} — NOT isolated[/dim]"
        )
    else:
        console.print(f"  [red]✗[/red] Sandbox   [dim]{sb['detail']}[/dim]")
        console.print(
            "              [dim]File-only steps still work; command steps will fail closed.[/dim]"
        )

    # 3. Ollama and the configured model
    from agent.llm import ollama_health

    oh = ollama_health()
    if not oh["reachable"]:
        console.print(f"  [red]✗[/red] Ollama    [dim]{oh['detail']}[/dim]")
    elif not oh["model_present"]:
        console.print(
            f"  [red]✗[/red] Model     [dim]{oh['detail']} (installed: {', '.join(oh['models']) or 'none'})[/dim]"
        )
    else:
        console.print(
            f"  [green]✓[/green] Ollama    [dim]{oh['url']} — model '{oh['model']}' installed[/dim]"
        )

    # 4. AgentOS API
    try:
        h = client.health()
        api_ok = h.get("status") == "ok"
        if api_ok:
            console.print(
                f"  [green]✓[/green] API       [dim]Reachable at {cfg.api_url}[/dim]"
            )
        else:
            console.print(
                f"  [red]✗[/red] API       [dim]Reachable but reported unwell: {h}[/dim]"
            )
    except APIError as e:
        console.print(f"  [red]✗[/red] API       [dim]Returned error: {e.detail}[/dim]")
    except (Exception, SystemExit):  # the HTTP client exits on connection errors
        console.print(
            f"  [red]✗[/red] API       [dim]Unreachable at {cfg.api_url}[/dim]"
        )

    # 5. Config
    if cfg.config_file.exists():
        console.print(
            f"  [green]✓[/green] Config    [dim]Found at {cfg.config_file}[/dim]"
        )
    else:
        console.print(
            "  [yellow]![/yellow] Config    [dim]No config.yaml found, using defaults[/dim]"
        )

    console.print()
