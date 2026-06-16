from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

console = Console()


def permission_table(permissions: list[str]) -> Table:
    table = Table(title="Requested Permissions", show_header=True, header_style="bold magenta")
    table.add_column("Permission", style="cyan")
    table.add_column("Risk", style="yellow")
    _RISK = {
        "internet": "Medium — can make outbound HTTP requests",
        "filesystem:read": "Low — reads module data directory only",
        "filesystem:write": "Low — writes module data directory only",
        "anthropic_api": "Medium — uses your Anthropic API key",
        "system": "HIGH — elevated system access",
    }
    for p in permissions:
        table.add_row(p, _RISK.get(p, "Unknown"))
    return table


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def error(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


def warning(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")


def info(msg: str) -> None:
    console.print(f"[bold blue]ℹ[/bold blue] {msg}")
