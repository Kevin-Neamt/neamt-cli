from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from neamt.bridge import handle_call
from neamt.loader import Skill, discover_skills


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SHELL_HTML = _TEMPLATES_DIR / "shell.html"

app = FastAPI(title="Neamt Dashboard", version="0.1.0")

_skills: dict[str, Skill] = {}


def _reload_skills() -> None:
    _skills.clear()
    for skill in discover_skills():
        _skills[skill.manifest.id] = skill
        if skill.enabled and skill.ui and skill.ui.exists():
            mount_path = f"/ui/{skill.manifest.id}"
            ui_dir = skill.ui if skill.ui.is_dir() else skill.ui.parent
            try:
                app.mount(
                    mount_path,
                    StaticFiles(directory=str(ui_dir), html=True),
                    name=f"{skill.manifest.id}-static",
                )
            except Exception:
                pass


_reload_skills()


class CallRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


@app.post("/api/skills/{skill_id}/call")
async def call_skill(skill_id: str, body: CallRequest) -> dict[str, Any]:
    skill = _skills.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return handle_call(skill, body.action, body.payload)


@app.get("/api/skills")
async def list_skills() -> list[dict[str, Any]]:
    return [
        {
            "id": skill.manifest.id,
            "name": skill.manifest.name,
            "version": skill.manifest.version,
            "enabled": skill.enabled,
            "dashboard": skill.manifest.dashboard.model_dump() if skill.manifest.dashboard else None,
        }
        for skill in _skills.values()
    ]


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def shell(full_path: str) -> HTMLResponse:
    nav_items = [
        {
            "id": skill.manifest.id,
            "label": skill.manifest.dashboard.nav_label if skill.manifest.dashboard else skill.manifest.name,
            "icon": skill.manifest.dashboard.nav_icon if skill.manifest.dashboard else "📦",
            "route": skill.manifest.dashboard.route if skill.manifest.dashboard else f"/ui/{skill.manifest.id}",
        }
        for skill in _skills.values()
        if skill.enabled and skill.manifest.dashboard
    ]
    import json
    nav_json = json.dumps(nav_items)
    html = _SHELL_HTML.read_text().replace("__NAV_ITEMS__", nav_json)
    return HTMLResponse(html)
