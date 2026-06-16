from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from neamt.permissions import PermissionGuard


_BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "requests",
        "httpx",
        "urllib",
        "urllib3",
        "subprocess",
        "os",
        "sys",
        "shutil",
        "socket",
        "asyncio",
        "threading",
        "importlib",
        "builtins",
        "ctypes",
    }
)

_REAL_IMPORT = builtins.__import__


def _make_restricted_import():
    def _restricted_import(name: str, *args: Any, **kwargs: Any):
        top = name.split(".")[0]
        if top in _BLOCKED_MODULES:
            raise ImportError(f"Import of '{name}' is blocked in sandbox")
        return _REAL_IMPORT(name, *args, **kwargs)

    return _restricted_import


def run_module_function(
    module_path: Path,
    function: str,
    payload: dict[str, Any],
    guard: PermissionGuard,
) -> Any:
    """
    Load *module_path* in a restricted sandbox and call *function* with *payload*.

    The sandbox replaces __import__ so that any attempt to import a blocked
    module raises ImportError immediately, both at module load time and at
    call time.
    """
    source = module_path.read_text()
    code = compile(source, str(module_path), "exec")

    restricted_builtins = vars(builtins).copy()
    restricted_builtins["__import__"] = _make_restricted_import()

    globs: dict[str, Any] = {
        "__builtins__": restricted_builtins,
        "__file__": str(module_path),
        "__name__": "__neamt_module__",
        "_neamt_guard": guard,
    }

    exec(code, globs)  # noqa: S102

    fn = globs.get(function)
    if fn is None or not callable(fn):
        raise AttributeError(f"Module has no callable '{function}'")

    return fn(**payload)
