"""
AgentOS CLI — `config` command
================================
Show current configuration and check API connectivity.

Usage:
    agentos config
    agentos config --set api_url http://myserver:8000
    agentos config --edit
"""

from __future__ import annotations

import subprocess
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from cli import client
from cli.client import APIError
from cli.config import _CONFIG_FILE, cfg

console = Console()


def show_config(
    edit: bool = typer.Option(False, "--edit", "-e", help="Open config file in $EDITOR."),
    set_key: Optional[str] = typer.Option(None, "--set", help="Set a config key=value, e.g. --set api_url=http://host:8000"),
) -> None:
    """Show current AgentOS configuration and API health."""

    # ── Set a value ──────────────────────────────────────────────────────────
    if set_key:
        if "=" not in set_key:
            console.print("[red]✗ Use --set key=value format, e.g. --set api_url=http://localhost:8000[/red]")
            raise typer.Exit(code=1)

        import yaml
        key, _, value = set_key.partition("=")
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if _CONFIG_FILE.exists():
            with _CONFIG_FILE.open() as f:
                existing = yaml.safe_load(f) or {}
        existing[key.strip()] = value.strip()
        with _CONFIG_FILE.open("w") as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=True)
        console.print(f"[green]✓ Set[/green] [bold]{key.strip()}[/bold] = {value.strip()}")
        console.print(f"[dim]Saved to {_CONFIG_FILE}[/dim]")
        return

    # ── Open in editor ───────────────────────────────────────────────────────
    if edit:
        import os
        editor = os.getenv("EDITOR", "nano")
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([editor, str(_CONFIG_FILE)], check=False)
        return

    # ── Display config ────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Key", style="cyan", min_width=30)
    table.add_column("Value", min_width=40)
    table.add_column("Source", style="dim", min_width=15)

    import os
    config_data = cfg.as_dict()
    sources = {
        "api_url": "AGENTOS_API_URL" if os.getenv("AGENTOS_API_URL") else "config.yaml",
        "default_model": "AGENTOS_MODEL" if os.getenv("AGENTOS_MODEL") else "config.yaml",
        "default_task_type": "config.yaml",
        "poll_interval_seconds": "config.yaml",
        "request_timeout_seconds": "config.yaml",
        "config_file": "—",
    }

    for key, value in config_data.items():
        table.add_row(key, str(value), sources.get(key, "config.yaml"))

    console.print()
    console.print("[bold]AgentOS CLI Configuration[/bold]")
    console.print(table)

    # ── Health check ─────────────────────────────────────────────────────────
    console.print()
    console.print("[bold]API Health Check[/bold]")
    try:
        h = client.health()
        status = h.get("status", "?")
        console.print(f"  [green]✓[/green] {cfg.api_url}  →  [green]{status}[/green]")
    except APIError as e:
        console.print(f"  [red]✗[/red] {cfg.api_url}  →  [red]{e.detail}[/red]")
    except Exception:
        console.print(f"  [red]✗[/red] {cfg.api_url}  →  [red]unreachable[/red]")
        console.print(f"    Is AgentOS running? Try: [cyan]make dev[/cyan]  or  [cyan]./start.sh[/cyan]")

    console.print()
    console.print(f"[dim]Config file: {_CONFIG_FILE}[/dim]")
    console.print(f"[dim]Edit:        agentos config --edit[/dim]")
    console.print(f"[dim]Override:    AGENTOS_API_URL=http://host:8000 agentos ...[/dim]")
    console.print()
