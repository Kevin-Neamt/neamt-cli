from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


_CONFIG_DIR = Path.home() / ".neamt"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_KEY_FILE = _CONFIG_DIR / ".fernet_key"

_ENCRYPTED_KEYS = {"anthropic_api_key", "openai_api_key"}


def _get_fernet() -> Fernet:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not _KEY_FILE.exists():
        _KEY_FILE.write_bytes(Fernet.generate_key())
        _KEY_FILE.chmod(0o600)
    return Fernet(_KEY_FILE.read_bytes())


def _load_raw() -> dict[str, Any]:
    if not _CONFIG_FILE.exists():
        return {}
    return json.loads(_CONFIG_FILE.read_text())


def _save_raw(data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))
    _CONFIG_FILE.chmod(0o600)


def get_config(key: str) -> Any:
    """Return config value; decrypts sensitive keys automatically."""
    raw = _load_raw()
    value = raw.get(key)
    if value is None:
        return None
    if key in _ENCRYPTED_KEYS and isinstance(value, str):
        return _get_fernet().decrypt(value.encode()).decode()
    return value


def set_config(key: str, value: Any) -> None:
    """Persist config value; encrypts sensitive keys automatically."""
    raw = _load_raw()
    if key in _ENCRYPTED_KEYS and isinstance(value, str):
        value = _get_fernet().encrypt(value.encode()).decode()
    raw[key] = value
    _save_raw(raw)


def list_config() -> dict[str, Any]:
    """Return all config keys; sensitive values are shown as '<encrypted>'."""
    raw = _load_raw()
    result: dict[str, Any] = {}
    for k, v in raw.items():
        result[k] = "<encrypted>" if k in _ENCRYPTED_KEYS else v
    return result
