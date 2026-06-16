from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from neamt.loader import discover_skills


_FIXTURES = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, dest: Path) -> Path:
    src = _FIXTURES / name
    target = dest / name
    shutil.copytree(src, target)
    return target


def test_valid_skill_loads(tmp_path: Path) -> None:
    _copy_fixture("valid_skill", tmp_path)
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    skill = skills[0]
    assert skill.manifest.id == "valid-skill"
    assert skill.enabled is True
    assert skill.entry.exists()


def test_directory_without_manifest_ignored(tmp_path: Path) -> None:
    no_manifest = tmp_path / "ghost-skill"
    no_manifest.mkdir()
    (no_manifest / "main.py").write_text("# nothing")
    skills = discover_skills(tmp_path)
    assert len(skills) == 0


def test_invalid_manifest_ignored(tmp_path: Path) -> None:
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "neamt.manifest.json").write_text("{not valid json")
    skills = discover_skills(tmp_path)
    assert len(skills) == 0


def test_disabled_skill_marked(tmp_path: Path) -> None:
    _copy_fixture("valid_skill", tmp_path)

    import neamt.loader as loader_mod
    original = loader_mod._DISABLED_FILE
    disabled_path = tmp_path / "disabled-skills"
    disabled_path.write_text("valid-skill\n")
    loader_mod._DISABLED_FILE = disabled_path
    try:
        skills = discover_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].enabled is False
    finally:
        loader_mod._DISABLED_FILE = original


def test_malicious_skill_manifest_loads(tmp_path: Path) -> None:
    """Malicious skill has valid manifest (empty perms) — should load as Skill."""
    _copy_fixture("malicious_skill", tmp_path)
    skills = discover_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].manifest.permissions == []
