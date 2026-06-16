from __future__ import annotations

import json
import pytest
from pathlib import Path

from neamt.manifest import Manifest, load_manifest


def _base(**overrides) -> dict:
    data = {
        "id": "my-skill",
        "name": "My Skill",
        "version": "1.0.0",
        "author": "Author",
        "description": "Test",
        "permissions": [],
        "entry": "main.py",
        "neamt_version": "0.1.0",
    }
    data.update(overrides)
    return data


def test_valid_manifest() -> None:
    m = Manifest.model_validate(_base())
    assert m.id == "my-skill"
    assert m.dashboard is None


def test_valid_manifest_with_dashboard() -> None:
    m = Manifest.model_validate(_base(dashboard={
        "nav_label": "My Skill",
        "nav_icon": "🚀",
        "route": "/my-skill",
        "ui": "ui/index.html",
    }))
    assert m.dashboard is not None
    assert m.dashboard.route == "/my-skill"


def test_valid_permissions() -> None:
    m = Manifest.model_validate(_base(permissions=["internet", "filesystem:read"]))
    assert "internet" in m.permissions


def test_invalid_permission() -> None:
    with pytest.raises(Exception, match="Unknown permission"):
        Manifest.model_validate(_base(permissions=["network"]))


def test_missing_required_field() -> None:
    data = _base()
    del data["name"]
    with pytest.raises(Exception):
        Manifest.model_validate(data)


def test_id_with_special_chars() -> None:
    with pytest.raises(Exception, match="slug"):
        Manifest.model_validate(_base(id="my skill!"))


def test_id_with_uppercase() -> None:
    with pytest.raises(Exception, match="slug"):
        Manifest.model_validate(_base(id="MySkill"))


def test_id_single_char() -> None:
    m = Manifest.model_validate(_base(id="a"))
    assert m.id == "a"


def test_id_starts_with_hyphen() -> None:
    with pytest.raises(Exception, match="slug"):
        Manifest.model_validate(_base(id="-bad"))


def test_load_manifest_from_file(tmp_path: Path) -> None:
    path = tmp_path / "neamt.manifest.json"
    path.write_text(json.dumps(_base()))
    m = load_manifest(path)
    assert m.id == "my-skill"
