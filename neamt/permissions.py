from __future__ import annotations


VALID_PERMISSIONS: frozenset[str] = frozenset(
    {
        "internet",
        "filesystem:read",
        "filesystem:write",
        "anthropic_api",
        "system",
    }
)


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission denied: '{permission}' not granted for this module")
        self.permission = permission


class PermissionGuard:
    def __init__(self, granted: list[str]) -> None:
        self._granted: frozenset[str] = frozenset(granted)

    def has(self, permission: str) -> bool:
        return permission in self._granted

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise PermissionDenied(permission)
