from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from neamt.permissions import VALID_PERMISSIONS


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$|^[a-z0-9]$")


class DashboardConfig(BaseModel):
    nav_label: str
    nav_icon: str = "📦"
    route: str
    ui: str  # relative path to the skill's UI entry (HTML/JS)


class Manifest(BaseModel):
    id: str
    name: str
    version: str
    author: str
    description: str
    permissions: list[str]
    entry: str
    neamt_version: str
    dashboard: Optional[DashboardConfig] = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"Skill id '{v}' must be an alphanumeric slug (lowercase letters, digits, hyphens; "
                "must start and end with alphanumeric)"
            )
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        invalid = [p for p in v if p not in VALID_PERMISSIONS]
        if invalid:
            raise ValueError(
                f"Unknown permission(s): {invalid}. Valid: {sorted(VALID_PERMISSIONS)}"
            )
        return v


def load_manifest(path: Path) -> Manifest:
    """Load and validate a neamt.manifest.json file."""
    data = json.loads(path.read_text())
    return Manifest.model_validate(data)
