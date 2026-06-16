from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from neamt.manifest import Manifest, load_manifest
from neamt.permissions import PermissionGuard


_SKILLS_DIR = Path.home() / ".neamt" / "skills"
_DISABLED_FILE = Path.home() / ".neamt" / "disabled-skills"


def _load_disabled() -> set[str]:
    if not _DISABLED_FILE.exists():
        return set()
    return {line.strip() for line in _DISABLED_FILE.read_text().splitlines() if line.strip()}


@dataclass
class Skill:
    path: Path
    manifest: Manifest
    guard: PermissionGuard
    enabled: bool

    @property
    def entry(self) -> Path:
        return self.path / self.manifest.entry

    @property
    def ui(self) -> Optional[Path]:
        if self.manifest.dashboard and self.manifest.dashboard.ui:
            return self.path / self.manifest.dashboard.ui
        return None


def discover_skills(skills_dir: Optional[Path] = None) -> list[Skill]:
    """Scan *skills_dir* for valid neamt skills and return them."""
    base = skills_dir or _SKILLS_DIR
    base.mkdir(parents=True, exist_ok=True)

    disabled = _load_disabled()
    skills: list[Skill] = []

    for candidate in sorted(base.iterdir()):
        if not candidate.is_dir():
            continue
        manifest_path = candidate / "neamt.manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = load_manifest(manifest_path)
        except Exception:
            continue

        guard = PermissionGuard(manifest.permissions)
        enabled = manifest.id not in disabled
        skills.append(Skill(path=candidate, manifest=manifest, guard=guard, enabled=enabled))

    return skills
