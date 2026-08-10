"""Tests for chwrite.backends' platform dispatch of scoped (deny-user/
deny-group) locks (SPEC.md 29) - protect_path_scoped/unprotect_path/
query_path's scope-aware branching, independent of any one OS backend's
own internals (those are covered per-backend elsewhere).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chwrite import backends
from chwrite.errors import ChwriteError


def test_protect_path_scoped_refuses_on_platform_without_scoped_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends, "PLATFORM", "posix")
    with pytest.raises(ChwriteError) as exc_info:
        backends.protect_path_scoped("/tmp/f.txt", ["bob"], [])
    assert exc_info.value.code == 2
    assert "posix" in str(exc_info.value)


def test_protect_path_scoped_dispatches_to_platform_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_protect = MagicMock(
        return_value={"backend": "fake-acl", "level": "ENFORCED", "hard": False}
    )
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(
        backends,
        "SCOPED_BACKENDS",
        {"macos": (fake_protect, MagicMock(), MagicMock())},
    )
    result = backends.protect_path_scoped("/tmp/f.txt", ["bob"], ["group1"])
    fake_protect.assert_called_once_with("/tmp/f.txt", ["bob"], ["group1"])
    assert result["backend"] == "fake-acl"


def test_unprotect_path_dispatches_to_scoped_backend_when_entry_has_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_unprotect = MagicMock()
    blanket_unprotect = MagicMock()
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(
        backends,
        "SCOPED_BACKENDS",
        {"macos": (MagicMock(), fake_unprotect, MagicMock())},
    )
    monkeypatch.setattr(
        backends, "BACKENDS", {"macos": (MagicMock(), blanket_unprotect, MagicMock())}
    )

    entry = {"scope": {"deny_user": ["bob"], "deny_group": []}}
    backends.unprotect_path("/tmp/f.txt", entry)  # type: ignore[arg-type]

    fake_unprotect.assert_called_once_with("/tmp/f.txt", entry)
    blanket_unprotect.assert_not_called()


def test_unprotect_path_uses_blanket_backend_for_all_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_unprotect = MagicMock()
    blanket_unprotect = MagicMock()
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(
        backends,
        "SCOPED_BACKENDS",
        {"macos": (MagicMock(), fake_unprotect, MagicMock())},
    )
    monkeypatch.setattr(
        backends, "BACKENDS", {"macos": (MagicMock(), blanket_unprotect, MagicMock())}
    )

    entry = {"scope": "all"}
    backends.unprotect_path("/tmp/f.txt", entry)  # type: ignore[arg-type]

    blanket_unprotect.assert_called_once_with("/tmp/f.txt", entry)
    fake_unprotect.assert_not_called()


def test_unprotect_path_defaults_to_blanket_when_scope_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No "scope" key at all (e.g. entries written before section 29
    existed) must behave exactly like "all" - never silently dispatch to a
    scoped backend it wasn't asked for."""
    fake_unprotect = MagicMock()
    blanket_unprotect = MagicMock()
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(
        backends,
        "SCOPED_BACKENDS",
        {"macos": (MagicMock(), fake_unprotect, MagicMock())},
    )
    monkeypatch.setattr(
        backends, "BACKENDS", {"macos": (MagicMock(), blanket_unprotect, MagicMock())}
    )

    entry = {}
    backends.unprotect_path("/tmp/f.txt", entry)  # type: ignore[arg-type]

    blanket_unprotect.assert_called_once_with("/tmp/f.txt", entry)
    fake_unprotect.assert_not_called()


def test_query_path_without_entry_uses_blanket_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    blanket_query = MagicMock(return_value=("ENFORCED", "fake-blanket"))
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(backends, "BACKENDS", {"macos": (MagicMock(), MagicMock(), blanket_query)})
    assert backends.query_path("/tmp/f.txt") == ("ENFORCED", "fake-blanket")


def test_query_path_with_scoped_entry_uses_scoped_backend_and_names_from_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_query = MagicMock(return_value=("ENFORCED", "fake-scoped"))
    monkeypatch.setattr(backends, "PLATFORM", "macos")
    monkeypatch.setattr(
        backends, "SCOPED_BACKENDS", {"macos": (MagicMock(), MagicMock(), scoped_query)}
    )
    entry = {"scope": {"deny_user": ["bob"], "deny_group": ["contractors"]}}

    result = backends.query_path("/tmp/f.txt", entry)  # type: ignore[arg-type]

    scoped_query.assert_called_once_with("/tmp/f.txt", ["bob"], ["contractors"])
    assert result == ("ENFORCED", "fake-scoped")


def test_query_path_scoped_entry_on_platform_without_scoped_backend_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blanket_query = MagicMock(return_value=("READONLY", "fake-blanket"))
    monkeypatch.setattr(backends, "PLATFORM", "posix")
    monkeypatch.setattr(backends, "BACKENDS", {"posix": (MagicMock(), MagicMock(), blanket_query)})
    entry = {"scope": {"deny_user": ["bob"], "deny_group": []}}

    result = backends.query_path("/tmp/f.txt", entry)  # type: ignore[arg-type]

    assert result == ("READONLY", "fake-blanket")


@pytest.mark.parametrize(
    ("system", "expected"),
    [("Darwin", "macos"), ("Linux", "linux"), ("Windows", "windows")],
)
def test_detect_platform_maps_known_systems(
    monkeypatch: pytest.MonkeyPatch, system: str, expected: str
) -> None:
    monkeypatch.setattr(backends.platform, "system", lambda: system)
    assert backends.detect_platform() == expected


def test_detect_platform_falls_back_to_posix_for_unknown_posix_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends.platform, "system", lambda: "SomeBSD")
    monkeypatch.setattr(backends.os, "name", "posix")
    assert backends.detect_platform() == "posix"


def test_detect_platform_falls_back_to_unknown_for_non_posix_unknown_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends.platform, "system", lambda: "SomeOS")
    monkeypatch.setattr(backends.os, "name", "somethingelse")
    assert backends.detect_platform() == "unknown"
