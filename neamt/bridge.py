from __future__ import annotations

from typing import Any

from neamt.loader import Skill
from neamt.sandbox import run_module_function


def handle_call(skill: Skill, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch *action* on *skill* inside the sandbox.

    Returns ``{"status": "ok", "result": ...}`` or ``{"status": "error", "message": ...}``.
    """
    if not skill.enabled:
        return {"status": "error", "message": f"Skill '{skill.manifest.id}' is disabled"}

    if not skill.entry.exists():
        return {"status": "error", "message": f"Entry point not found: {skill.entry}"}

    try:
        result = run_module_function(skill.entry, action, payload, skill.guard)
        return {"status": "ok", "result": result}
    except ImportError as exc:
        return {"status": "error", "message": f"Sandbox violation: {exc}"}
    except AttributeError as exc:
        return {"status": "error", "message": f"Unknown action '{action}': {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
