from __future__ import annotations

import typer
from rich.panel import Panel

from cli.output import console, error
from neamt.loader import discover_skills


def info_cmd(skill_id: str) -> None:
    skills = {skill.manifest.id: skill for skill in discover_skills()}
    skill = skills.get(skill_id)
    if skill is None:
        error(f"Skill '{skill_id}' not found")
        raise typer.Exit(1)

    lines = [
        f"[bold]Name:[/bold]         {skill.manifest.name}",
        f"[bold]Version:[/bold]      {skill.manifest.version}",
        f"[bold]Author:[/bold]       {skill.manifest.author}",
        f"[bold]Description:[/bold]  {skill.manifest.description}",
        f"[bold]Permissions:[/bold]  {', '.join(skill.manifest.permissions) or 'none'}",
        f"[bold]Entry:[/bold]        {skill.entry}",
        f"[bold]Status:[/bold]       {'enabled' if skill.enabled else 'disabled'}",
        f"[bold]neamt_version:[/bold] {skill.manifest.neamt_version}",
    ]
    console.print(Panel("\n".join(lines), title=f"[cyan]{skill_id}[/cyan]", expand=False))
