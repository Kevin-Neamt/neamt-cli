from __future__ import annotations

from neamt.cli.output import console, error, success
from neamt.config import get_config, list_config, set_config


def config_get(key: str) -> None:
    value = get_config(key)
    if value is None:
        error(f"Key '{key}' not set")
    else:
        console.print(f"[cyan]{key}[/cyan] = {value}")


def config_set(key: str, value: str) -> None:
    set_config(key, value)
    success(f"Set '{key}'")


def config_list() -> None:
    data = list_config()
    if not data:
        console.print("[dim]No configuration set[/dim]")
        return
    for k, v in data.items():
        console.print(f"[cyan]{k}[/cyan] = {v}")
