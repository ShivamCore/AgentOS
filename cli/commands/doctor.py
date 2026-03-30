"""
AgentOS CLI — `doctor` command
================================
Run system diagnostics to ensure AgentOS can operate fully.

Usage:
    agentos doctor
"""

from __future__ import annotations

import sys
import subprocess
import requests
import typer
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
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 11):
        console.print(f"  [green]✓[/green] Python    [dim]v{py_ver} (>= 3.11)[/dim]")
    else:
        console.print(f"  [red]✗[/red] Python    [dim]v{py_ver} (requires >= 3.11)[/dim]")

    # 2. Docker
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        console.print("  [green]✓[/green] Docker    [dim]Installed and reachable[/dim]")
    except Exception:
        console.print("  [yellow]![/yellow] Docker    [dim]Unreachable or not installed (sandbox will fallback to local subprocess)[/dim]")

    # 3. Ollama
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            console.print("  [green]✓[/green] Ollama    [dim]Running on localhost:11434[/dim]")
        else:
            console.print("  [red]✗[/red] Ollama    [dim]Running but returned error[/dim]")
    except Exception:
        console.print("  [red]✗[/red] Ollama    [dim]Unreachable at localhost:11434[/dim]")

    # 4. AgentOS API
    try:
        h = client.health()
        api_ok = h.get("status") == "ok"
        if api_ok:
            console.print(f"  [green]✓[/green] API       [dim]Reachable at {cfg.api_url}[/dim]")
        else:
            console.print(f"  [red]✗[/red] API       [dim]Reachable but reported unwell: {h}[/dim]")
    except APIError as e:
        console.print(f"  [red]✗[/red] API       [dim]Returned error: {e.detail}[/dim]")
    except Exception:
        console.print(f"  [red]✗[/red] API       [dim]Unreachable at {cfg.api_url}[/dim]")

    # 5. Config
    if cfg.config_file.exists():
        console.print(f"  [green]✓[/green] Config    [dim]Found at {cfg.config_file}[/dim]")
    else:
        console.print("  [yellow]![/yellow] Config    [dim]No config.yaml found, using defaults[/dim]")

    console.print()
