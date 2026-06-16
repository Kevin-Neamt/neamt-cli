from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import typer
from rich.prompt import Prompt

from neamt.cli.output import console, error, info, permission_table, success, warning
from neamt.manifest import load_manifest
from neamt.permissions import PermissionGuard
from neamt.sandbox import run_module_function


_SKILLS_DIR = Path.home() / ".neamt" / "skills"


def install(source: str) -> None:
    """Install a skill from a GitHub URL or local path."""
    with console.status("[bold]Cloning / copying skill…"):
        tmp = Path(tempfile.mkdtemp())
        try:
            if source.startswith("http") or source.startswith("git@"):
                ret = subprocess.run(["git", "clone", "--depth=1", source, str(tmp / "skill")], capture_output=True)
                if ret.returncode != 0:
                    error(f"git clone failed: {ret.stderr.decode()}")
                    raise typer.Exit(1)
                src_dir = tmp / "skill"
            else:
                src_dir = Path(source).expanduser().resolve()
                if not src_dir.is_dir():
                    error(f"'{source}' is not a directory")
                    raise typer.Exit(1)

            manifest_path = src_dir / "neamt.manifest.json"
            if not manifest_path.exists():
                error("neamt.manifest.json not found in skill root")
                raise typer.Exit(1)

            try:
                manifest = load_manifest(manifest_path)
            except Exception as exc:
                error(f"Invalid manifest: {exc}")
                raise typer.Exit(1)

        finally:
            pass  # keep tmp alive until copy done below

    info(f"Skill: [bold]{manifest.name}[/bold] v{manifest.version} by {manifest.author}")
    info(manifest.description)

    if manifest.permissions:
        console.print(permission_table(manifest.permissions))
        if "system" in manifest.permissions:
            answer = Prompt.ask("[bold red]This skill requests SYSTEM permission. Type CONFIRM to proceed[/bold red]")
            if answer.strip() != "CONFIRM":
                warning("Installation cancelled")
                raise typer.Exit(0)
        else:
            answer = Prompt.ask("Install this skill? [y/N]")
            if answer.strip().lower() != "y":
                warning("Installation cancelled")
                raise typer.Exit(0)

    dest = _SKILLS_DIR / manifest.id
    if dest.exists():
        warning(f"Skill '{manifest.id}' already exists — overwriting")
        shutil.rmtree(dest)

    with console.status("[bold]Installing…"):
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dest)

        install_script = dest / "install.py"
        if install_script.exists():
            guard = PermissionGuard(manifest.permissions)
            try:
                run_module_function(install_script, "install", {}, guard)
            except AttributeError:
                pass  # install() not defined — that's fine
            except Exception as exc:
                warning(f"install() script error (non-fatal): {exc}")

    success(f"Installed skill [bold]{manifest.name}[/bold] → {dest}")
