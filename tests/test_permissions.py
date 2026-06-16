from __future__ import annotations

import pytest

from neamt.permissions import PermissionDenied, PermissionGuard


def test_has_granted() -> None:
    g = PermissionGuard(["internet", "filesystem:read"])
    assert g.has("internet") is True
    assert g.has("filesystem:read") is True


def test_has_not_granted() -> None:
    g = PermissionGuard(["internet"])
    assert g.has("filesystem:write") is False
    assert g.has("anthropic_api") is False


def test_has_empty() -> None:
    g = PermissionGuard([])
    assert g.has("internet") is False


def test_require_granted_does_not_raise() -> None:
    g = PermissionGuard(["internet"])
    g.require("internet")  # should not raise


def test_require_not_granted_raises() -> None:
    g = PermissionGuard(["internet"])
    with pytest.raises(PermissionDenied) as exc_info:
        g.require("anthropic_api")
    assert "anthropic_api" in str(exc_info.value)


def test_permission_denied_attribute() -> None:
    g = PermissionGuard([])
    try:
        g.require("system")
    except PermissionDenied as e:
        assert e.permission == "system"
    else:
        pytest.fail("PermissionDenied not raised")
