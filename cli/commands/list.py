from __future__ import annotations

from rich.table import Table

from cli.output import console, info
from neamt.loader import discover_skills


def list_skills() -> None:
    skills = discover_skills()
    if not skills:
        info("No skills installed")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Author", style="dim")
    table.add_column("Status")

    for skill in skills:
        status = "[green]enabled[/green]" if skill.enabled else "[red]disabled[/red]"
        table.add_row(skill.manifest.id, skill.manifest.name, skill.manifest.version, skill.manifest.author, status)

    console.print(table)
