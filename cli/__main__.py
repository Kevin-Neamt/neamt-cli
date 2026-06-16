from __future__ import annotations

import typer

from neamt import __version__

app = typer.Typer(name="neamt", help="Neamt skill manager", add_completion=False)


@app.command("install")
def install_cmd(source: str = typer.Argument(..., help="GitHub URL or local path")) -> None:
    """Install a skill."""
    from cli.commands.install import install
    install(source)


@app.command("uninstall")
def uninstall_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Uninstall a skill."""
    from cli.commands.uninstall import uninstall
    uninstall(skill_id)


@app.command("list")
def list_cmd() -> None:
    """List installed skills."""
    from cli.commands.list import list_skills
    list_skills()


@app.command("update")
def update_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Update a skill (requires git install)."""
    from cli.commands.update import update
    update(skill_id)


@app.command("info")
def info_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Show skill details."""
    from cli.commands.info import info_cmd as _info
    _info(skill_id)


@app.command("enable")
def enable_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Enable a disabled skill."""
    from cli.commands.toggle import enable
    enable(skill_id)


@app.command("disable")
def disable_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Disable a skill without uninstalling it."""
    from cli.commands.toggle import disable
    disable(skill_id)


@app.command("start")
def start_cmd(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Start the dashboard server."""
    from cli.commands.server import start
    start(host, port)


@app.command("stop")
def stop_cmd() -> None:
    """Stop the dashboard server."""
    from cli.commands.server import stop
    stop()


@app.command("restart")
def restart_cmd(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Restart the dashboard server."""
    from cli.commands.server import restart
    restart(host, port)


@app.command("config")
def config_cmd(
    action: str = typer.Argument(..., help="get | set | list"),
    key: str = typer.Argument("", help="Config key"),
    value: str = typer.Argument("", help="Config value (for set)"),
) -> None:
    """Manage configuration (get/set/list)."""
    from cli.commands.config import config_get, config_list, config_set
    if action == "list":
        config_list()
    elif action == "get":
        config_get(key)
    elif action == "set":
        config_set(key, value)
    else:
        typer.echo(f"Unknown action '{action}'. Use: get | set | list", err=True)
        raise typer.Exit(1)


@app.command("doctor")
def doctor_cmd() -> None:
    """Run system health checks."""
    from cli.commands.doctor import doctor
    doctor()


@app.command("version")
def version_cmd() -> None:
    """Print version."""
    typer.echo(f"neamt-core {__version__}")


if __name__ == "__main__":
    app()
