from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from neamt.permissions import PermissionGuard


_DATA_ROOT = Path.home() / ".neamt" / "data"


class HttpAPI:
    def __init__(self, guard: PermissionGuard) -> None:
        self._guard = guard

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._guard.require("internet")
        return httpx.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self._guard.require("internet")
        return httpx.post(url, **kwargs)


class FilesystemAPI:
    def __init__(self, guard: PermissionGuard, skill_id: str) -> None:
        self._guard = guard
        self._base = _DATA_ROOT / skill_id

    def _resolve(self, relative: str) -> Path:
        path = (self._base / relative).resolve()
        if not str(path).startswith(str(self._base.resolve())):
            raise PermissionError("Path escapes skill data directory")
        return path

    def read(self, relative: str) -> str:
        self._guard.require("filesystem:read")
        return self._resolve(relative).read_text()

    def write(self, relative: str, content: str) -> None:
        self._guard.require("filesystem:write")
        path = self._resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class AIApi:
    def __init__(self, guard: PermissionGuard) -> None:
        self._guard = guard
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            from neamt.config import get_config
            api_key = get_config("anthropic_api_key")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def complete(self, prompt: str, model: str = "claude-haiku-4-5-20251001", max_tokens: int = 1024) -> str:
        self._guard.require("anthropic_api")
        client = self._get_client()
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


class CoreAPI:
    def __init__(self, guard: PermissionGuard, skill_id: str) -> None:
        self.http = HttpAPI(guard)
        self.fs = FilesystemAPI(guard, skill_id)
        self.ai = AIApi(guard)
