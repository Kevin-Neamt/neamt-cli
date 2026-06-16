from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from cli.output import error, info, success, warning


_SKILLS_DIR = Path.home() / ".neamt" / "skills"


def update(skill_id: str) -> None:
    dest = _SKILLS_DIR / skill_id
    if not dest.exists():
        error(f"Skill '{skill_id}' not installed")
        raise typer.Exit(1)

    git_dir = dest / ".git"
    if not git_dir.exists():
        warning("Skill was not installed via git — cannot auto-update")
        raise typer.Exit(1)

    info(f"Updating skill '{skill_id}'…")
    ret = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], capture_output=True, text=True)
    if ret.returncode != 0:
        error(f"git pull failed: {ret.stderr}")
        raise typer.Exit(1)

    success(f"Updated skill '{skill_id}'")
    if ret.stdout.strip():
        info(ret.stdout.strip())
