from __future__ import annotations

import socket
import sys
from pathlib import Path

from rich.table import Table

from cli.output import console
from neamt.config import get_config
from neamt.loader import discover_skills
from neamt import __version__


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def doctor() -> None:
    checks: list[tuple[str, bool, str]] = []

    # Python version
    ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", ok, f"Found {sys.version.split()[0]}" if ok else f"Upgrade required (found {sys.version.split()[0]})"))

    # Core version
    checks.append(("neamt-core installed", True, f"v{__version__}"))

    # Dashboard port free
    free = _port_available(8000)
    checks.append(("Port 8000 available", free, "Free" if free else "In use — pass --port to use another"))

    # Anthropic API key
    key = get_config("anthropic_api_key")
    has_key = bool(key)
    checks.append(("Anthropic API key configured", has_key, "Set" if has_key else "Run: neamt config set anthropic_api_key <key>"))

    # Skills
    skills = discover_skills()
    for skill in skills:
        entry_ok = skill.entry.exists()
        checks.append((
            f"Skill '{skill.manifest.id}' entry exists",
            entry_ok,
            str(skill.entry) if entry_ok else f"Missing {skill.entry} — reinstall skill",
        ))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="dim")

    for label, ok, detail in checks:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        table.add_row(label, status, detail)

    console.print(table)
    all_ok = all(ok for _, ok, _ in checks)
    if all_ok:
        console.print("[bold green]All checks passed[/bold green]")
    else:
        console.print("[bold yellow]Some checks failed — see details above[/bold yellow]")
