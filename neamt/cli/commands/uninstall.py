from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.prompt import Prompt

from neamt.cli.output import error, success, warning


_SKILLS_DIR = Path.home() / ".neamt" / "skills"


def uninstall(skill_id: str) -> None:
    dest = _SKILLS_DIR / skill_id
    if not dest.exists():
        error(f"Skill '{skill_id}' not installed")
        raise typer.Exit(1)

    answer = Prompt.ask(f"Remove skill '{skill_id}'? [y/N]")
    if answer.strip().lower() != "y":
        warning("Cancelled")
        raise typer.Exit(0)

    shutil.rmtree(dest)
    success(f"Uninstalled skill '{skill_id}'")
