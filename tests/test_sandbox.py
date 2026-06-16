from __future__ import annotations

import pytest
from pathlib import Path

from neamt.sandbox import run_module_function
from neamt.permissions import PermissionGuard

_FIXTURES = Path(__file__).parent / "fixtures"
_VALID = _FIXTURES / "valid_skill" / "main.py"
_MALICIOUS = _FIXTURES / "malicious_skill" / "main.py"


def _guard(*perms: str) -> PermissionGuard:
    return PermissionGuard(list(perms))


# ── Malicious skill must raise ImportError for each blocked import ──

@pytest.mark.parametrize("blocked", ["requests", "subprocess", "os", "socket"])
def test_blocked_import(blocked: str, tmp_path: Path) -> None:
    src = f"import {blocked}\ndef run(): return {{}}\n"
    mod = tmp_path / "mod.py"
    mod.write_text(src)
    with pytest.raises(ImportError, match=blocked):
        run_module_function(mod, "run", {}, _guard())


def test_malicious_skill_raises_on_load() -> None:
    """Loading the malicious fixture itself must fail at exec time."""
    with pytest.raises(ImportError):
        run_module_function(_MALICIOUS, "run", {}, _guard())


# ── Valid skill executes correctly ──

def test_valid_skill_hello() -> None:
    result = run_module_function(_VALID, "hello", {"name": "World"}, _guard("internet"))
    assert result == {"message": "Hello World"}


def test_valid_skill_unknown_function() -> None:
    with pytest.raises(AttributeError):
        run_module_function(_VALID, "nonexistent", {}, _guard("internet"))
