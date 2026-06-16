from __future__ import annotations

from pathlib import Path

import typer

from neamt.cli.output import error, success
from neamt.loader import discover_skills


_DISABLED_FILE = Path.home() / ".neamt" / "disabled-skills"


def _load_disabled() -> set[str]:
    if not _DISABLED_FILE.exists():
        return set()
    return {l.strip() for l in _DISABLED_FILE.read_text().splitlines() if l.strip()}


def _save_disabled(ids: set[str]) -> None:
    _DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DISABLED_FILE.write_text("\n".join(sorted(ids)))


def enable(skill_id: str) -> None:
    skills = {skill.manifest.id for skill in discover_skills()}
    if skill_id not in skills:
        error(f"Skill '{skill_id}' not found")
        raise typer.Exit(1)
    disabled = _load_disabled()
    disabled.discard(skill_id)
    _save_disabled(disabled)
    success(f"Enabled skill '{skill_id}'")


def disable(skill_id: str) -> None:
    skills = {skill.manifest.id for skill in discover_skills()}
    if skill_id not in skills:
        error(f"Skill '{skill_id}' not found")
        raise typer.Exit(1)
    disabled = _load_disabled()
    disabled.add(skill_id)
    _save_disabled(disabled)
    success(f"Disabled skill '{skill_id}'")
